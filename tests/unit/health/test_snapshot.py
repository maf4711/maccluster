"""Health snapshot aggregate."""

from __future__ import annotations

from datetime import UTC, datetime
from ipaddress import IPv4Address, IPv4Network

from maccluster.domain.enums import OverallHealth, ReachabilityState
from maccluster.domain.models import ClusterConfig, Node, NodeHealth
from maccluster.health.aggregate import exit_code_for_snapshot
from maccluster.health.snapshot import build_snapshot


def _cfg():
    nodes = (
        Node("a", ("a",), IPv4Address("10.42.0.1"), "u1"),
        Node("b", ("b",), IPv4Address("10.42.0.2"), "u2"),
    )
    return ClusterConfig(1, "c", IPv4Network("10.42.0.0/24"), "bridge0", nodes)


def test_healthy():
    cfg = _cfg()
    nhs = [
        NodeHealth(cfg.nodes[0], ReachabilityState.UP),
        NodeHealth(cfg.nodes[1], ReachabilityState.UP),
    ]
    snap = build_snapshot(
        timestamp=datetime.now(UTC),
        cfg=cfg,
        self_node_id="a",
        node_health=nhs,
    )
    assert snap.overall == OverallHealth.HEALTHY
    assert exit_code_for_snapshot(snap) == 0


def test_degraded_peer_down():
    cfg = _cfg()
    nhs = [
        NodeHealth(cfg.nodes[0], ReachabilityState.UP),
        NodeHealth(cfg.nodes[1], ReachabilityState.DOWN),
    ]
    snap = build_snapshot(
        timestamp=datetime.now(UTC),
        cfg=cfg,
        self_node_id="a",
        node_health=nhs,
    )
    assert snap.overall == OverallHealth.DEGRADED
    assert exit_code_for_snapshot(snap) == 3
