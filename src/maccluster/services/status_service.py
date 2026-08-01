"""Status snapshot orchestration."""

from __future__ import annotations

from maccluster.app_factory import AppContext
from maccluster.domain.enums import LinkState, NodeRole, ReachabilityState
from maccluster.domain.models import HealthSnapshot, NodeHealth
from maccluster.health.aggregate import exit_code_for_snapshot
from maccluster.health.snapshot import build_snapshot
from maccluster.services.config_service import load_and_bind_self
from maccluster.services.tb_service import probe_tb


def collect_status(ctx: AppContext) -> tuple[HealthSnapshot, int]:
    cfg, self_node = load_and_bind_self(ctx)
    try:
        tb = probe_tb(ctx)
    except Exception:
        tb = None

    bridge = None
    try:
        bridge = ctx.net_read.get_bridge(cfg.bridge_interface)
    except Exception:
        bridge = None

    # Local link summary
    local_link = LinkState.UNKNOWN
    if tb and tb.ports:
        if any(p.link_state == LinkState.CONNECTED for p in tb.ports):
            local_link = LinkState.CONNECTED
        elif all(p.link_state == LinkState.UNCONNECTED for p in tb.ports):
            local_link = LinkState.UNCONNECTED

    node_health: list[NodeHealth] = []
    for node in cfg.nodes:
        if node.id == self_node.id or node.role == NodeRole.SELF:
            node_health.append(
                NodeHealth(
                    node=node,
                    reachability=ReachabilityState.UP,
                    link_state=local_link,
                    notes="self",
                )
            )
            continue
        # Ping peer IP
        try:
            pr = ctx.reachability.ping(str(node.ip))
            state = pr.state
            rtt = pr.rtt_ms
        except Exception:
            state = ReachabilityState.UNKNOWN
            rtt = None

        if cfg.ssh_probes_enabled and state != ReachabilityState.UP:
            target = node.ssh_target or str(node.ip)
            try:
                sr = ctx.reachability.ssh_probe(target)
                if sr.state == ReachabilityState.UP:
                    state = ReachabilityState.UP
            except Exception:
                pass

        node_health.append(
            NodeHealth(
                node=node,
                reachability=state,
                link_state=LinkState.UNKNOWN,
                rtt_ms=rtt,
            )
        )

    snap = build_snapshot(
        timestamp=ctx.clock.now(),
        cfg=cfg,
        self_node_id=self_node.id,
        node_health=node_health,
        bridge=bridge,
        tb=tb,
    )
    return snap, exit_code_for_snapshot(snap)
