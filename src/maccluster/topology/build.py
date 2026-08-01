"""Build Topology from TB snapshot + config (pure)."""

from __future__ import annotations

from maccluster.domain.enums import LinkState, ReachabilityState
from maccluster.domain.models import (
    ClusterConfig,
    Node,
    ThunderboltSnapshot,
    Topology,
    TopologyLink,
)
from maccluster.topology.match import match_peer_hint, topology_complete


def build_topology(
    *,
    cfg: ClusterConfig,
    tb: ThunderboltSnapshot,
    self_node: Node | None,
    reachability: dict[str, ReachabilityState] | None = None,
) -> Topology:
    reachability = reachability or {}
    links: list[TopologyLink] = []
    unmatched: list[str] = []
    peers = [n for n in cfg.nodes if self_node is None or n.id != self_node.id]

    for port in tb.ports:
        matched = match_peer_hint(port.peer_name, cfg.nodes)
        if port.peer_name and not matched and port.link_state == LinkState.CONNECTED:
            unmatched.append(port.peer_name)
        links.append(
            TopologyLink(
                local_receptacle=port.receptacle_id,
                peer_hint=port.peer_name,
                domain_uuid=port.domain_uuid,
                link_state=port.link_state,
                matched_node_id=matched,
                speed_gbps=port.link_speed_gbps,
            )
        )

    complete = topology_complete(
        peer_nodes=peers,
        reachability=reachability,
        links=links,
    )
    return Topology(
        links=tuple(links),
        unmatched_peers=tuple(dict.fromkeys(unmatched)),
        complete=complete,
    )
