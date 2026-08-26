"""Build health snapshot structures (pure assembly helpers)."""

from __future__ import annotations

from datetime import datetime

from maccluster.domain.enums import LinkState, OverallHealth, ReachabilityState
from maccluster.domain.models import (
    BridgeInterface,
    ClusterConfig,
    ExoCorrelation,
    HealHeartbeat,
    HealthSnapshot,
    InterfaceTraffic,
    MeshHealth,
    NodeHealth,
    RdmaStatus,
    ThunderboltSnapshot,
)
from maccluster.health.mesh import build_mesh_health


def build_snapshot(
    *,
    timestamp: datetime,
    cfg: ClusterConfig,
    self_node_id: str | None,
    node_health: list[NodeHealth] | tuple[NodeHealth, ...],
    bridge: BridgeInterface | None = None,
    tb: ThunderboltSnapshot | None = None,
    traffic: tuple[InterfaceTraffic, ...] = (),
    mesh: MeshHealth | None = None,
    rdma: RdmaStatus | None = None,
    heal_heartbeat: HealHeartbeat | None = None,
    exo: ExoCorrelation | None = None,
) -> HealthSnapshot:
    overall = aggregate_overall(node_health, self_node_id=self_node_id)
    if mesh is None:
        mesh = build_mesh_health(node_health, self_node_id=self_node_id, bridge=bridge, tb=tb)
    return HealthSnapshot(
        timestamp=timestamp,
        cluster_name=cfg.name,
        self_node_id=self_node_id,
        nodes=tuple(node_health),
        overall=overall,
        bridge=bridge,
        tb=tb,
        traffic=traffic,
        mesh=mesh,
        rdma=rdma,
        heal_heartbeat=heal_heartbeat,
        exo=exo,
    )


def aggregate_overall(
    nodes: list[NodeHealth] | tuple[NodeHealth, ...],
    *,
    self_node_id: str | None,
) -> OverallHealth:
    if not nodes:
        return OverallHealth.UNKNOWN
    self_ok = True
    peer_down = False
    for nh in nodes:
        if self_node_id and nh.node.id == self_node_id:
            # Self is local — treat as up unless explicitly down
            if nh.reachability == ReachabilityState.DOWN:
                self_ok = False
        else:
            if nh.reachability == ReachabilityState.DOWN:
                peer_down = True
            elif nh.reachability == ReachabilityState.UNKNOWN and not self_node_id:
                pass
    if not self_ok:
        return OverallHealth.UNHEALTHY
    if peer_down:
        return OverallHealth.DEGRADED
    return OverallHealth.HEALTHY


def any_tb_link(tb: ThunderboltSnapshot | None) -> bool:
    if not tb:
        return False
    return any(p.link_state == LinkState.CONNECTED for p in tb.ports)
