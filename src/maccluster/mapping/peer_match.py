"""Peer ↔ cluster node matching (pure): controller UID, then domain UUID, then hostname.

The controller UID is stable across reboots and wins whenever the attached
device exposes one; the domain UUID is only as fresh as the last
``config refresh-tb``; a hostname hint is the last resort (system_profiler
names a peer Mac only by model code such as ``Mac16,11``).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from maccluster.domain.models import Node
from maccluster.mapping.tb_identity import normalize_uid, normalize_uuid

__all__ = ["PeerMatch", "match_hostname", "match_node"]


@dataclass(frozen=True)
class PeerMatch:
    node_id: str
    by: str  # uid | domain | hostname


def match_node(
    *,
    nodes: Sequence[Node],
    peer_uid: object = None,
    peer_domain_uuid: object = None,
    peer_hint: str | None = None,
) -> PeerMatch | None:
    uid = normalize_uid(peer_uid)
    if uid:
        for n in nodes:
            if uid in {normalize_uid(u) for u in n.tb_controller_uids}:
                return PeerMatch(n.id, "uid")
    uuid = normalize_uuid(peer_domain_uuid)
    if uuid:
        for n in nodes:
            if uuid in {normalize_uuid(u) for u in n.tb_domain_uuids}:
                return PeerMatch(n.id, "domain")
    host = match_hostname(peer_hint, nodes)
    return PeerMatch(host, "hostname") if host else None


def match_hostname(hint: str | None, nodes: Sequence[Node]) -> str | None:
    if not hint:
        return None
    h = hint.strip().lower()
    if not h:
        return None
    for n in nodes:
        if n.id.lower() == h:
            return n.id
        for host in n.hostnames:
            low = host.lower()
            if low == h or low.split(".")[0] == h:
                return n.id
    return None
