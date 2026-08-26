"""Interface counter sampling and rate computation (pure + small I/O helpers)."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from maccluster.constants import (
    TRAFFIC_CACHE_DIR_NAME,
    TRAFFIC_CACHE_FILE_NAME,
    TRAFFIC_MAX_DT_S,
    TRAFFIC_MIN_DT_S,
)
from maccluster.domain.models import InterfaceCounters, InterfaceTraffic

# netstat -ib / netstat -I <if> -b header:
# Name Mtu Network Address Ipkts Ierrs Ibytes Opkts Oerrs Obytes Coll
_NETSTAT_LINK_RE = re.compile(
    r"^(?P<name>\S+)\s+"
    r"(?P<mtu>\d+)\s+"
    r"<Link#\d+>\s+"
    r"(?P<addr>\S+)?\s*"
    r"(?P<ipkts>\d+)\s+"
    r"(?P<ierrs>\d+)\s+"
    r"(?P<ibytes>\d+)\s+"
    r"(?P<opkts>\d+)\s+"
    r"(?P<oerrs>\d+)\s+"
    r"(?P<obytes>\d+)\s+"
    r"(?P<coll>\d+)\s*$"
)


def parse_netstat_ib(text: str, *, t_mono: float | None = None) -> dict[str, InterfaceCounters]:
    """Parse `netstat -ib` or `netstat -I <iface> -b` output; keep Link# rows only."""
    t = time.monotonic() if t_mono is None else t_mono
    out: dict[str, InterfaceCounters] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("Name"):
            continue
        if "<Link#" not in line:
            continue
        m = _NETSTAT_LINK_RE.match(line)
        if not m:
            # Address may be empty → extra spaces; try looser split from the right
            c = _parse_link_loose(line, t)
            if c is not None:
                out[c.name] = c
            continue
        name = m.group("name")
        out[name] = InterfaceCounters(
            name=name,
            ipkts=int(m.group("ipkts")),
            ierrs=int(m.group("ierrs")),
            ibytes=int(m.group("ibytes")),
            opkts=int(m.group("opkts")),
            oerrs=int(m.group("oerrs")),
            obytes=int(m.group("obytes")),
            coll=int(m.group("coll")),
            t_mono=t,
        )
    return out


def _parse_link_loose(line: str, t_mono: float) -> InterfaceCounters | None:
    """Fallback when MAC address field makes fixed regex miss."""
    if "<Link#" not in line:
        return None
    parts = line.split()
    # Expect: name mtu <Link#N> [mac] ipkts ierrs ibytes opkts oerrs obytes coll
    if len(parts) < 10:
        return None
    name = parts[0]
    # Last 7 numeric fields
    nums: list[int] = []
    for p in reversed(parts):
        if p.isdigit():
            nums.append(int(p))
            if len(nums) == 7:
                break
        elif p.startswith("<Link"):
            break
    if len(nums) != 7:
        return None
    coll, obytes, oerrs, opkts, ibytes, ierrs, ipkts = nums
    return InterfaceCounters(
        name=name,
        ipkts=ipkts,
        ierrs=ierrs,
        ibytes=ibytes,
        opkts=opkts,
        oerrs=oerrs,
        obytes=obytes,
        coll=coll,
        t_mono=t_mono,
    )


def compute_traffic(
    curr: InterfaceCounters,
    prev: InterfaceCounters | None,
    *,
    min_dt: float = TRAFFIC_MIN_DT_S,
    max_dt: float = TRAFFIC_MAX_DT_S,
) -> InterfaceTraffic:
    """Build InterfaceTraffic; rates only when prev is valid and Δt in range."""
    base = InterfaceTraffic(
        name=curr.name,
        ibytes=curr.ibytes,
        obytes=curr.obytes,
        ipkts=curr.ipkts,
        opkts=curr.opkts,
        ierrs=curr.ierrs,
        oerrs=curr.oerrs,
        coll=curr.coll,
    )
    if prev is None or prev.name != curr.name:
        return base
    dt = curr.t_mono - prev.t_mono
    if dt < min_dt or dt > max_dt:
        return base
    # Counters can reset on interface recreate
    if (
        curr.ibytes < prev.ibytes
        or curr.obytes < prev.obytes
        or curr.ipkts < prev.ipkts
        or curr.opkts < prev.opkts
    ):
        return InterfaceTraffic(
            name=curr.name,
            ibytes=curr.ibytes,
            obytes=curr.obytes,
            ipkts=curr.ipkts,
            opkts=curr.opkts,
            ierrs=curr.ierrs,
            oerrs=curr.oerrs,
            coll=curr.coll,
            sample_dt_s=dt,
            rate_available=False,
        )
    d_ib = curr.ibytes - prev.ibytes
    d_ob = curr.obytes - prev.obytes
    d_ip = curr.ipkts - prev.ipkts
    d_op = curr.opkts - prev.opkts
    d_ie = max(0, curr.ierrs - prev.ierrs)
    d_oe = max(0, curr.oerrs - prev.oerrs)
    return InterfaceTraffic(
        name=curr.name,
        ibytes=curr.ibytes,
        obytes=curr.obytes,
        ipkts=curr.ipkts,
        opkts=curr.opkts,
        ierrs=curr.ierrs,
        oerrs=curr.oerrs,
        coll=curr.coll,
        rx_bps=(d_ib * 8) / dt,
        tx_bps=(d_ob * 8) / dt,
        rx_pps=d_ip / dt,
        tx_pps=d_op / dt,
        ierrs_delta=d_ie,
        oerrs_delta=d_oe,
        sample_dt_s=dt,
        rate_available=True,
    )


def format_bps(bps: float | None) -> str:
    if bps is None:
        return "n/a"
    if bps >= 1e9:
        return f"{bps / 1e9:.2f} Gb/s"
    if bps >= 1e6:
        return f"{bps / 1e6:.2f} Mb/s"
    if bps >= 1e3:
        return f"{bps / 1e3:.1f} kb/s"
    return f"{bps:.0f} b/s"


def format_pps(pps: float | None) -> str:
    if pps is None:
        return "n/a"
    if pps >= 1e6:
        return f"{pps / 1e6:.2f}M pps"
    if pps >= 1e3:
        return f"{pps / 1e3:.1f}k pps"
    return f"{pps:.0f} pps"


def default_cache_path() -> Path:
    base = Path.home() / "Library" / "Caches" / TRAFFIC_CACHE_DIR_NAME
    # Also honor XDG if set (rare on macOS)
    xdg = Path.home() / ".cache" / TRAFFIC_CACHE_DIR_NAME
    # Prefer macOS Library/Caches when on Darwin-style home layout
    if (Path.home() / "Library").is_dir():
        return base / TRAFFIC_CACHE_FILE_NAME
    return xdg / TRAFFIC_CACHE_FILE_NAME


def counters_to_dict(c: InterfaceCounters) -> dict[str, Any]:
    return {
        "name": c.name,
        "ipkts": c.ipkts,
        "ierrs": c.ierrs,
        "ibytes": c.ibytes,
        "opkts": c.opkts,
        "oerrs": c.oerrs,
        "obytes": c.obytes,
        "coll": c.coll,
        "t_mono": c.t_mono,
        "t_wall": time.time(),
    }


def counters_from_dict(d: dict[str, Any], *, t_mono: float | None = None) -> InterfaceCounters:
    # Prefer wall-clock delta to reconstruct monotonic approximation for file cache
    wall = float(d.get("t_wall", 0.0))
    if t_mono is None:
        # Map old wall time into current monotonic space: t_mono_now - (now_wall - old_wall)
        age = max(0.0, time.time() - wall) if wall else 0.0
        t_mono = time.monotonic() - age
    return InterfaceCounters(
        name=str(d["name"]),
        ipkts=int(d["ipkts"]),
        ierrs=int(d["ierrs"]),
        ibytes=int(d["ibytes"]),
        opkts=int(d["opkts"]),
        oerrs=int(d["oerrs"]),
        obytes=int(d["obytes"]),
        coll=int(d.get("coll", 0)),
        t_mono=t_mono,
    )


def load_counter_cache(path: Path) -> dict[str, InterfaceCounters]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        items = data.get("ifaces", {})
        out: dict[str, InterfaceCounters] = {}
        for name, raw in items.items():
            if isinstance(raw, dict):
                out[name] = counters_from_dict(raw)
        return out
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        return {}


def save_counter_cache(path: Path, counters: dict[str, InterfaceCounters]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "ifaces": {k: counters_to_dict(v) for k, v in counters.items()},
    }
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(path)


class TrafficSampler:
    """Stateful sampler for monitor (in-memory) with optional disk cache for status."""

    def __init__(self, *, use_disk_cache: bool = True, cache_path: Path | None = None) -> None:
        self._prev: dict[str, InterfaceCounters] = {}
        self._use_disk = use_disk_cache
        self._cache_path = cache_path or default_cache_path()
        if use_disk_cache:
            self._prev = load_counter_cache(self._cache_path)

    @property
    def previous(self) -> dict[str, InterfaceCounters]:
        return dict(self._prev)

    def observe(
        self,
        current: dict[str, InterfaceCounters],
        *,
        persist: bool = True,
    ) -> tuple[InterfaceTraffic, ...]:
        rows: list[InterfaceTraffic] = []
        for name in sorted(current.keys()):
            curr = current[name]
            prev = self._prev.get(name)
            rows.append(compute_traffic(curr, prev))
        self._prev = dict(current)
        if persist and self._use_disk:
            try:
                save_counter_cache(self._cache_path, self._prev)
            except OSError:
                pass
        return tuple(rows)
