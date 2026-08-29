"""Topology peer matching (pure)."""

from __future__ import annotations

from maccluster.domain.enums import LinkState, ReachabilityState
from maccluster.domain.models import Node, TopologyLink
from maccluster.mapping.peer_match import match_hostname, match_node


def match_peer(
    *,
    peer_hint: str | None,
    peer_domain_uuid: str | None,
    nodes: tuple[Node, ...] | list[Node],
    peer_uid: str | None = None,
) -> str | None:
    """Match a TB link to a config node: controller UID, then peer Domain UUID, then name.

    system_profiler reports the peer only by model code (e.g. "Mac16,11"),
    which is ambiguous with two identical Macs — the peer port's Domain UUID
    (nested device block) is the only locally visible unique key for a Mac;
    the controller UID (``tb_controller_uids``) is stable when exposed.
    """
    m = match_node(
        nodes=nodes, peer_uid=peer_uid, peer_domain_uuid=peer_domain_uuid, peer_hint=peer_hint
    )
    return m.node_id if m else None


def match_peer_hint(
    hint: str | None,
    nodes: tuple[Node, ...] | list[Node],
) -> str | None:
    return match_hostname(hint, nodes)


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
