"""Topology peer matching (pure)."""

from __future__ import annotations

from maccluster.domain.enums import LinkState, ReachabilityState
from maccluster.domain.models import Node, TopologyLink


def match_peer(
    *,
    peer_hint: str | None,
    peer_domain_uuid: str | None,
    nodes: tuple[Node, ...] | list[Node],
) -> str | None:
    """Match a TB link to a config node: peer Domain UUID first, name fallback.

    system_profiler reports the peer only by model code (e.g. "Mac16,11"),
    which is ambiguous with two identical Macs — the peer port's Domain UUID
    (nested device block) is the only locally visible unique key.
    """
    if peer_domain_uuid:
        u = peer_domain_uuid.lower()
        for n in nodes:
            if any(d.lower() == u for d in n.tb_domain_uuids):
                return n.id
    return match_peer_hint(peer_hint, nodes)


def match_peer_hint(
    hint: str | None,
    nodes: tuple[Node, ...] | list[Node],
) -> str | None:
    if not hint:
        return None
    h = hint.lower()
    for n in nodes:
        if n.id.lower() == h:
            return n.id
        for host in n.hostnames:
            if host.lower() == h or host.lower().split(".")[0] == h:
                return n.id
    return None


def topology_complete(
    *,
    peer_nodes: list[Node] | tuple[Node, ...],
    reachability: dict[str, ReachabilityState],
    links: list[TopologyLink] | tuple[TopologyLink, ...],
) -> bool:
    """ADR-0006: peer complete if Ping-up OR Domain/Link match."""
    matched_ids = {lnk.matched_node_id for lnk in links if lnk.matched_node_id}
    link_up = any(lnk.link_state == LinkState.CONNECTED for lnk in links)
    for peer in peer_nodes:
        ping_ok = reachability.get(str(peer.ip), ReachabilityState.UNKNOWN) == ReachabilityState.UP
        link_ok = peer.id in matched_ids or link_up
        if not (ping_ok or link_ok):
            return False
    return True
