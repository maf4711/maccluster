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
    sntp: str | None = None,
    sntp_missing: bool = False,
    error: str | None = None,
) -> HostSnapshot:
    used, free = parse_vm_stat_ram_gb(vm_stat)
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
        return snapshot_from_raw(
            node_id,
            vm_stat=str(data.get("vm_stat") or ""),
            df=str(data.get("df") or ""),
            uptime=str(data.get("uptime") or ""),
            pmset=str(data.get("pmset") or ""),
            sntp=None if sntp_val is None else str(sntp_val),
            sntp_missing=missing,
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
