"""Thunderbolt identities (pure): which IDs name a Mac's TB buses, and are they current?

macOS regenerates every bus's *domain UUID* on reboot, so ``tb_domain_uuids`` in
cluster.toml go stale silently and ``maccluster topo`` stops matching peers.
The controller/switch UID of each bus (``system_profiler SPThunderboltDataType
-json`` → ``switch_uid_key``, e.g. ``0x05AC51E771159CF0``) never changes; it is
also what IORegistry shows as the decimal ``UID`` and, byte-reversed, as the
``node_guid`` of the ``rdma_enX`` device on that bus.

Verified on node-a (macOS 27.0): a peer *Mac* exposes only its domain UUID
locally, not its UID; a Studio Display exposes both.
"""

from __future__ import annotations

import json
import re
from typing import Any

from maccluster.domain.enums import CheckSeverity, LinkState
from maccluster.domain.models import DoctorFinding, Node, ThunderboltPort, ThunderboltSnapshot

__all__ = [
    "check_tb_identity",
    "controller_uid_from_node_guid",
    "live_controller_uids",
    "live_domain_uuids",
    "normalize_uid",
    "normalize_uuid",
    "parse_system_profiler_json",
]

JSON_SOURCE = "system_profiler-json"
_SPEED_RE = re.compile(r"([\d.]+)\s*Gb/s", re.I)
_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
_HEX_RE = re.compile(r"^[0-9a-f]{1,16}$", re.I)
_GUID_RE = re.compile(r"^[0-9a-f]{4}(:[0-9a-f]{4}){3}$", re.I)


# --- canonical forms ---------------------------------------------------------------------


def normalize_uid(value: object) -> str | None:
    """Canonical ``0x`` + 16 upper-case hex digits; None when *value* is not a UID.

    Accepts system_profiler (``0x05AC51E771159CF0``), IORegistry decimal
    (``408791720660409584``, also as int) and bare hex with letters.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return f"0x{value:016X}" if value >= 0 else None
    raw = str(value).strip()
    if not raw:
        return None
    low = raw.lower()
    try:
        if low.startswith("0x"):
            number = int(low[2:], 16)
        elif low.isdigit():
            number = int(low, 10)
        elif _HEX_RE.match(low):
            number = int(low, 16)
        else:
            return None
    except ValueError:
        return None
    return f"0x{number:016X}"


def normalize_uuid(value: object) -> str | None:
    """Upper-case RFC-4122 text form; None for anything that is not a UUID."""
    if value is None:
        return None
    raw = str(value).strip()
    return raw.upper() if _UUID_RE.match(raw) else None


def controller_uid_from_node_guid(guid: object) -> str | None:
    """``rdma_enX`` IORegistry ``node_guid`` (``f09c:1571:e751:ac05``) → bus UID.

    The GUID is the switch UID in reversed byte order; this is how an RDMA
    device maps onto a Thunderbolt bus without positional guessing.
    """
    raw = str(guid or "").strip()
    if not _GUID_RE.match(raw):
        return None
    data = bytes.fromhex(raw.replace(":", ""))
    return normalize_uid(int.from_bytes(data[::-1], "big"))


# --- system_profiler -json ---------------------------------------------------------------


def parse_system_profiler_json(text: str) -> ThunderboltSnapshot:
    """``system_profiler SPThunderboltDataType -json`` → snapshot, one port per bus.

    Ports come back ordered by receptacle number. Garbage → empty snapshot.
    """
    empty = ThunderboltSnapshot(ports=(), source=JSON_SOURCE)
    try:
        data = json.loads(text)
    except (ValueError, RecursionError, TypeError):
        return empty
    buses = data.get("SPThunderboltDataType") if isinstance(data, dict) else None
    if not isinstance(buses, list):
        return empty
    ports: list[ThunderboltPort] = []
    host_model: str | None = None
    for bus in buses:
        if not isinstance(bus, dict):
            continue
        model = _text(bus.get("device_name_key"))
        if host_model is None and model:
            host_model = model
        ports.extend(_bus_ports(bus))
    ports.sort(key=lambda p: (_receptacle_sort_key(p.receptacle_id), p.receptacle_id))
    return ThunderboltSnapshot(ports=tuple(ports), source=JSON_SOURCE, host_model=host_model)


def _bus_ports(bus: dict[str, Any]) -> list[ThunderboltPort]:
    items = bus.get("_items")
    peer = items[0] if isinstance(items, list) and items and isinstance(items[0], dict) else {}
    out: list[ThunderboltPort] = []
    for key, tag in bus.items():
        if not (key.startswith("receptacle_") and key.endswith("_tag") and isinstance(tag, dict)):
            continue
        status = (_text(tag.get("receptacle_status_key")) or "").lower()
        if "no_device" in status:
            link = LinkState.UNCONNECTED
        elif "connected" in status:
            link = LinkState.CONNECTED
        else:
            link = LinkState.UNKNOWN
        connected = link == LinkState.CONNECTED
        peer_name = _text(peer.get("device_name_key")) or _text(peer.get("_name"))
        out.append(
            ThunderboltPort(
                receptacle_id=_text(tag.get("receptacle_id_key")) or "?",
                interface_name=None,
                capable=True,
                thunderbolt_version="USB4/TB",
                link_speed_gbps=_speed(tag.get("current_speed_key")),
                link_state=link,
                domain_uuid=_text(bus.get("domain_uuid_key")),
                peer_name=peer_name if connected else None,
                bus_uid=_text(bus.get("switch_uid_key")),
                status_raw=_text(tag.get("receptacle_status_key")),
                peer_mode=_text(peer.get("mode_key")) if connected else None,
                peer_domain_uuid=_text(peer.get("domain_uuid_key")) if connected else None,
                peer_uid=_text(peer.get("switch_uid_key")) if connected else None,
            )
        )
    return out


def _text(value: object) -> str | None:
    if value is None or isinstance(value, bool | dict | list):
        return None
    s = str(value).strip()
    return s or None


def _speed(value: object) -> float | None:
    m = _SPEED_RE.search(str(value or ""))
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


def _receptacle_sort_key(rid: str) -> int:
    return int(rid) if rid.isdigit() else 1 << 30


# --- live ids from any snapshot ----------------------------------------------------------


def live_domain_uuids(tb: ThunderboltSnapshot | None) -> tuple[str, ...]:
    """This Mac's own bus domain UUIDs, in port order, canonical, de-duplicated."""
    return _unique(normalize_uuid(p.domain_uuid) for p in (tb.ports if tb else ()))


def live_controller_uids(tb: ThunderboltSnapshot | None) -> tuple[str, ...]:
    """This Mac's own bus controller UIDs, in port order, canonical, de-duplicated."""
    return _unique(normalize_uid(p.bus_uid) for p in (tb.ports if tb else ()))


def _unique(values) -> tuple[str, ...]:
    return tuple(dict.fromkeys(v for v in values if v))


# --- doctor: are cluster.toml's ids for this Mac still current? -------------------------

_REFRESH_HINT = "maccluster config refresh-tb --dry-run"


def check_tb_identity(self_node: Node | None, tb: ThunderboltSnapshot | None) -> DoctorFinding:
    """``tb_ids`` finding: WARN ``tb_domain_uuids stale`` when none of this Mac's live
    domain UUIDs is in cluster.toml but its controller UIDs are (a reboot renamed the
    buses). Without UIDs to confirm the Mac, the mismatch is only INFO."""
    cid = "tb_ids"
    if tb is None or not tb.ports:
        return DoctorFinding(cid, CheckSeverity.INFO, "tb ids not probed", "")
    if self_node is None:
        return DoctorFinding(cid, CheckSeverity.INFO, "tb ids: self unknown", "")
    live_uuids = live_domain_uuids(tb)
    live_uids = live_controller_uids(tb)
    if not live_uuids and not live_uids:
        return DoctorFinding(cid, CheckSeverity.INFO, "no live TB domain UUIDs / UIDs", tb.source)
    cfg_uuids = _unique(normalize_uuid(u) for u in self_node.tb_domain_uuids)
    cfg_uids = _unique(normalize_uid(u) for u in self_node.tb_controller_uids)
    uuid_hits = [u for u in live_uuids if u in cfg_uuids]
    uid_hits = [u for u in live_uids if u in cfg_uids]
    if uuid_hits:
        return DoctorFinding(
            cid,
            CheckSeverity.OK,
            "tb_domain_uuids current",
            f"{len(uuid_hits)}/{len(live_uuids)} live UUIDs in cluster.toml",
        )
    if not cfg_uuids and not cfg_uids:
        return DoctorFinding(
            cid,
            CheckSeverity.INFO,
            f"no tb ids for {self_node.id} in cluster.toml",
            f"pin tb_domain_uuids + tb_controller_uids: {_REFRESH_HINT}",
        )
    live = ", ".join(live_uuids) or "-"
    if cfg_uuids and uid_hits:
        return DoctorFinding(
            cid,
            CheckSeverity.WARN,
            "tb_domain_uuids stale",
            f"live UUIDs [{live}] match none of {len(cfg_uuids)} in cluster.toml; "
            f"controller UIDs confirm this Mac (rebooted?) — {_REFRESH_HINT}",
        )
    if cfg_uuids and not cfg_uids:
        return DoctorFinding(
            cid,
            CheckSeverity.INFO,
            "tb_domain_uuids unverified",
            f"live UUIDs [{live}] match none in cluster.toml; no tb_controller_uids "
            f"to confirm this Mac — {_REFRESH_HINT}",
        )
    if not cfg_uuids and uid_hits:
        return DoctorFinding(
            cid,
            CheckSeverity.INFO,
            "tb_controller_uids current (no tb_domain_uuids)",
            f"{len(uid_hits)}/{len(live_uids)} live UIDs in cluster.toml — {_REFRESH_HINT}",
        )
    return DoctorFinding(
        cid,
        CheckSeverity.WARN,
        "tb ids mismatch",
        f"neither live UUIDs [{live}] nor UIDs [{', '.join(live_uids) or '-'}] are in "
        f"cluster.toml for {self_node.id} — config from another Mac? {_REFRESH_HINT}",
    )
