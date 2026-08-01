"""Status service."""

from __future__ import annotations

from maccluster.services.status_service import collect_status


def test_status_degraded(fake_ctx):
    snap, code = collect_status(fake_ctx)
    assert snap.self_node_id == "node-a"
    assert code == 3  # peer .4 down
    assert any(nh.reachability.value == "down" for nh in snap.nodes)
