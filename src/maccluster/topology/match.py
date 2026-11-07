"""Topology peer matching (pure)."""

from __future__ import annotations

from collections.abc import Sequence

from maccluster.domain.cable import is_mac_peer_name
from maccluster.domain.enums import LinkState, ReachabilityState
from maccluster.domain.models import Node, ThunderboltPort, ThunderboltSnapshot, TopologyLink
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


def ports_by_peer(
    *,
    tb: ThunderboltSnapshot | None,
    peers: Sequence[Node],
) -> dict[str, tuple[ThunderboltPort, ...]]:
    """node_id → the connected local TB ports whose attached device IS that peer.

    Each port is attributed via the 0.4.0 identity chain (controller UID →
    peer Domain UUID → hostname hint), so every peer row can show ITS link's
    negotiated rate instead of the machine's best Mac↔Mac link. Unmatched
    ports are attributed only in the one unambiguous case: a single configured
    peer and a single connected Mac-peer port. Anything else stays unmapped —
    a missing rate is better than another peer's rate.
    """
    if tb is None:
        return {}
    connected = [p for p in tb.ports if p.link_state == LinkState.CONNECTED]
    out: dict[str, list[ThunderboltPort]] = {}
    for port in connected:
        m = match_node(
            nodes=peers,
            peer_uid=port.peer_uid,
            peer_domain_uuid=port.peer_domain_uuid,
            peer_hint=port.peer_name,
        )
        if m:
            out.setdefault(m.node_id, []).append(port)
    if not out and len(peers) == 1:
        mac_ports = [p for p in connected if is_mac_peer_name(p.peer_name)]
        if len(mac_ports) == 1:
            out[peers[0].id] = mac_ports
    return {node_id: tuple(ports) for node_id, ports in out.items()}


def best_link_speed(ports: Sequence[ThunderboltPort]) -> float | None:
    """Highest trained rate among ONE peer's ports (dual-cable peers train two)."""
    speeds = [p.link_speed_gbps for p in ports if p.link_speed_gbps is not None]
    return max(speeds) if speeds else None


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
