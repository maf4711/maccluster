"""Topology peer matching (pure)."""

from __future__ import annotations

from maccluster.domain.enums import LinkState, ReachabilityState
from maccluster.domain.models import Node, TopologyLink


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
