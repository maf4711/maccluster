"""Partial cluster does not crash."""

from __future__ import annotations

from maccluster.domain.enums import ReachabilityState
from maccluster.services.status_service import collect_status
from maccluster.services.topo_service import collect_topology


def test_partial(fake_ctx):
    fake_ctx.reachability.default = ReachabilityState.DOWN
    fake_ctx.reachability.states = {}
    snap, code = collect_status(fake_ctx)
    assert code == 3
    assert snap.nodes
    topo = collect_topology(fake_ctx)
    assert topo.links is not None
