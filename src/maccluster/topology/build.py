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
from maccluster.mapping.peer_match import match_node
from maccluster.topology.match import topology_complete


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
        match = match_node(
            nodes=cfg.nodes,
            peer_uid=port.peer_uid,
            peer_domain_uuid=port.peer_domain_uuid,
            peer_hint=port.peer_name,
        )
        if port.peer_name and not match and port.link_state == LinkState.CONNECTED:
            unmatched.append(port.peer_name)
        links.append(
            TopologyLink(
                local_receptacle=port.receptacle_id,
                peer_hint=port.peer_name,
                domain_uuid=port.domain_uuid,
                link_state=port.link_state,
                matched_node_id=match.node_id if match else None,
                speed_gbps=port.link_speed_gbps,
                peer_domain_uuid=port.peer_domain_uuid,
                peer_uid=port.peer_uid,
                matched_by=match.by if match else None,
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
