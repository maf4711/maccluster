"""Status exit codes via service."""

from __future__ import annotations

from maccluster.domain.enums import ReachabilityState
from maccluster.services.status_service import collect_status


def test_all_up_exit_0(fake_ctx):
    fake_ctx.reachability.states = {
        "10.42.0.2": ReachabilityState.UP,
        "10.42.0.3": ReachabilityState.UP,
        "10.42.0.4": ReachabilityState.UP,
    }
    fake_ctx.reachability.default = ReachabilityState.UP
    _snap, code = collect_status(fake_ctx)
    assert code == 0


def test_peer_down_exit_3(fake_ctx):
    _snap, code = collect_status(fake_ctx)
    assert code == 3
