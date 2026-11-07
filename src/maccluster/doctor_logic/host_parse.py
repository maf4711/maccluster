"""Pure parsers for macOS host snapshots (no subprocess)."""

from __future__ import annotations

import json
import re
from typing import Any

from maccluster.domain.models import HostSnapshot

_PAGE_SIZE_RE = re.compile(r"page size of\s+(\d+)\s+bytes", re.IGNORECASE)
_PAGES_RE = re.compile(r"^Pages\s+([^:]+):\s+(\d+)\.?\s*$", re.MULTILINE)
_LOAD_RE = re.compile(r"load averages?:\s+([0-9]+[.,][0-9]+)", re.IGNORECASE)
_CPU_LIMIT_RE = re.compile(r"CPU_Speed_Limit\s*=\s*(\d+)", re.IGNORECASE)
_SNTP_PAREN_RE = re.compile(r"offset[^\n]*\(\s*([+-]?\d+\.\d+)\s*\)", re.IGNORECASE)
_SNTP_EQ_RE = re.compile(r"offset\s*[:=]\s*([+-]?\d+\.\d+)", re.IGNORECASE)
_SNTP_PLUSMINUS_RE = re.compile(r"([+-]\d+\.\d+)\s+\+/-")
# `pmset -g` rows; case-sensitive so "Sleep On Power Button" never matches, and
# anchored so "disksleep"/"displaysleep" never match. Trailing text is allowed
# ("sleep 1 (sleep prevented by powerd, ...)").
_PMSET_SLEEP_RE = re.compile(r"^\s*sleep\s+(\d+)", re.MULTILINE)
_PMSET_POWERNAP_RE = re.compile(r"^\s*powernap\s+(\d+)", re.MULTILINE)


def _to_float(text: str) -> float:
    return float(text.replace(",", "."))


def parse_vm_stat_ram_gb(text: str) -> tuple[float | None, float | None]:
    """used, free in GiB. used=(active+wired)*pagesize; free=(free+inactive)*pagesize."""
    if not text.strip():
        return None, None
    m = _PAGE_SIZE_RE.search(text)
    if not m:
        return None, None
    page = int(m.group(1))
    if page <= 0:
        return None, None
    counts: dict[str, int] = {}
    for label, raw in _PAGES_RE.findall(text):
        key = label.strip().lower()
        counts[key] = int(raw)
    active = counts.get("active")
    wired = counts.get("wired down")
    free = counts.get("free")
    inactive = counts.get("inactive")
    gib = 1024**3
    used_gb = None
    free_gb = None
    if active is not None and wired is not None:
        used_gb = (active + wired) * page / gib
    if free is not None and inactive is not None:
        free_gb = (free + inactive) * page / gib
    return used_gb, free_gb


def parse_df_free_gb(text: str) -> float | None:
    """POSIX `df -P` Available column is 512-byte blocks."""
    for line in reversed(text.splitlines()):
        parts = line.split()
        if len(parts) < 5:
            continue
        if parts[0].lower() == "filesystem":
            continue
        try:
            avail_blocks = int(parts[3])
        except ValueError:
            continue
        return avail_blocks * 512 / (1024**3)
    return None


def parse_uptime_load_1m(text: str) -> float | None:
    m = _LOAD_RE.search(text)
    if not m:
        return None
    try:
        return _to_float(m.group(1))
    except ValueError:
        return None


def parse_pmset_cpu_limit(text: str) -> int | None:
    m = _CPU_LIMIT_RE.search(text)
    if not m:
        return None
    return int(m.group(1))


def parse_rdma_enabled(text: str, returncode: int | None = None) -> bool | None:
    """Parse `rdma_ctl status` output. None = ambiguous/unknown."""
    low = text.lower()
    if "enabled" in low and "disabled" not in low:
        return True
    if "disabled" in low:
        return False
    for line in low.splitlines():
        s = line.strip()
        if s == "enabled" or s.endswith(": enabled"):
            return True
        if s == "disabled" or s.endswith(": disabled"):
            return False
    if returncode == 0 and "enable" in low:
        return True
    return None


def parse_pmset_power(text: str) -> tuple[int | None, bool | None]:
    """(sleep_minutes, powernap_enabled) from `pmset -g`. None = not reported."""
    sleep_m = _PMSET_SLEEP_RE.search(text)
    nap_m = _PMSET_POWERNAP_RE.search(text)
    sleep = int(sleep_m.group(1)) if sleep_m else None
    powernap = (int(nap_m.group(1)) != 0) if nap_m else None
    return sleep, powernap


def parse_sntp_offset_s(text: str) -> float | None:
    if not text.strip():
        return None
    matches = _SNTP_PAREN_RE.findall(text)
    if matches:
        return float(matches[-1])
    matches = _SNTP_EQ_RE.findall(text)
    if matches:
        return float(matches[-1])
    matches = _SNTP_PLUSMINUS_RE.findall(text)
    if matches:
        return float(matches[-1])
    return None


def snapshot_from_raw(
    node_id: str,
    *,
    vm_stat: str = "",
    df: str = "",
    uptime: str = "",
    pmset: str = "",
    pmset_g: str | None = None,
    sntp: str | None = None,
    sntp_missing: bool = False,
    rdma: str | None = None,
    rdma_missing: bool | None = None,
    error: str | None = None,
) -> HostSnapshot:
    """`rdma_missing=None` means RDMA was not probed on this path (e.g. local host)."""
    used, free = parse_vm_stat_ram_gb(vm_stat)
    sleep_minutes, powernap_enabled = parse_pmset_power(pmset_g) if pmset_g else (None, None)
    if rdma_missing is None:
        rdma_tool_available: bool | None = None
        rdma_enabled: bool | None = None
    elif rdma_missing:
        rdma_tool_available = False
        rdma_enabled = None
    else:
        rdma_tool_available = True
        rdma_enabled = parse_rdma_enabled(rdma or "")
    return HostSnapshot(
        node_id=node_id,
        ram_used_gb=used,
        ram_free_gb=free,
        load_1m=parse_uptime_load_1m(uptime),
        disk_free_gb=parse_df_free_gb(df),
        cpu_speed_limit_pct=parse_pmset_cpu_limit(pmset),
        ntp_offset_s=None if sntp_missing else parse_sntp_offset_s(sntp or ""),
        error=error,
        ntp_missing=sntp_missing,
        rdma_tool_available=rdma_tool_available,
        rdma_enabled=rdma_enabled,
        sleep_minutes=sleep_minutes,
        powernap_enabled=powernap_enabled,
    )


def _last_json_object(text: str) -> dict[str, Any]:
    blob = text.strip()
    for line in reversed(blob.splitlines()):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            blob = line
            break
    data = json.loads(blob)
    if not isinstance(data, dict):
        raise ValueError("host snapshot JSON must be an object")
    return data


def _opt_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def _opt_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


def snapshot_from_json(node_id: str, text: str) -> HostSnapshot:
    """Parse one JSON line: raw tool dumps or already-numeric HostSnapshot fields."""
    try:
        data = _last_json_object(text)
    except (json.JSONDecodeError, ValueError) as exc:
        return HostSnapshot(
            node_id=node_id,
            ram_used_gb=None,
            ram_free_gb=None,
            load_1m=None,
            disk_free_gb=None,
            cpu_speed_limit_pct=None,
            ntp_offset_s=None,
            error=f"host json parse failed: {exc}",
        )
    if "vm_stat" in data or "df" in data or "uptime" in data:
        sntp_val = data.get("sntp")
        missing = bool(data.get("sntp_missing"))
        if sntp_val is None and "sntp_missing" not in data:
            missing = True
        rdma_val = data.get("rdma")
        rdma_missing = bool(data.get("rdma_missing")) if "rdma_missing" in data else None
        pmset_g_val = data.get("pmset_g")
        return snapshot_from_raw(
            node_id,
            vm_stat=str(data.get("vm_stat") or ""),
            df=str(data.get("df") or ""),
            uptime=str(data.get("uptime") or ""),
            pmset=str(data.get("pmset") or ""),
            pmset_g=None if pmset_g_val is None else str(pmset_g_val),
            sntp=None if sntp_val is None else str(sntp_val),
            sntp_missing=missing,
            rdma=None if rdma_val is None else str(rdma_val),
            rdma_missing=rdma_missing,
            error=str(data["error"]) if data.get("error") else None,
        )
    try:
        return HostSnapshot(
            node_id=node_id,
            ram_used_gb=_opt_float(data.get("ram_used_gb")),
            ram_free_gb=_opt_float(data.get("ram_free_gb")),
            load_1m=_opt_float(data.get("load_1m")),
            disk_free_gb=_opt_float(data.get("disk_free_gb")),
            cpu_speed_limit_pct=_opt_int(data.get("cpu_speed_limit_pct")),
            ntp_offset_s=_opt_float(data.get("ntp_offset_s")),
            error=str(data["error"]) if data.get("error") else None,
            ntp_missing=bool(data.get("ntp_missing")),
            rdma_tool_available=(
                None
                if data.get("rdma_tool_available") is None
                else bool(data["rdma_tool_available"])
            ),
            rdma_enabled=(None if data.get("rdma_enabled") is None else bool(data["rdma_enabled"])),
            sleep_minutes=_opt_int(data.get("sleep_minutes")),
            powernap_enabled=(
                None if data.get("powernap_enabled") is None else bool(data["powernap_enabled"])
            ),
        )
    except (TypeError, ValueError) as exc:
        return HostSnapshot(
            node_id=node_id,
            ram_used_gb=None,
            ram_free_gb=None,
            load_1m=None,
            disk_free_gb=None,
            cpu_speed_limit_pct=None,
            ntp_offset_s=None,
            error=f"host json parse failed: {exc}",
        )
