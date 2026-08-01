"""Topology map service."""

from __future__ import annotations

from maccluster.app_factory import AppContext
from maccluster.domain.enums import ReachabilityState
from maccluster.domain.models import Topology
from maccluster.services.config_service import load_and_bind_self
from maccluster.services.tb_service import probe_tb
from maccluster.topology.build import build_topology


def collect_topology(ctx: AppContext) -> Topology:
    cfg, self_node = load_and_bind_self(ctx)
    tb = probe_tb(ctx)
    reach: dict[str, ReachabilityState] = {}
    for node in cfg.nodes:
        if node.id == self_node.id:
            continue
        try:
            pr = ctx.reachability.ping(str(node.ip))
            reach[str(node.ip)] = pr.state
        except Exception:
            reach[str(node.ip)] = ReachabilityState.UNKNOWN
    return build_topology(cfg=cfg, tb=tb, self_node=self_node, reachability=reach)
