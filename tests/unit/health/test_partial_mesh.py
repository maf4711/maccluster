"""Partial mesh health."""

from __future__ import annotations

from datetime import UTC, datetime
from ipaddress import IPv4Address, IPv4Network

from maccluster.domain.enums import OverallHealth, ReachabilityState
from maccluster.domain.models import ClusterConfig, Node, NodeHealth
from maccluster.health.snapshot import build_snapshot


def test_two_of_four_down():
    nodes = tuple(
        Node(f"n{i}", (f"n{i}",), IPv4Address(f"10.42.0.{i}"), f"u{i}") for i in range(1, 5)
    )
    cfg = ClusterConfig(1, "c", IPv4Network("10.42.0.0/24"), "bridge0", nodes)
    nhs = [
        NodeHealth(nodes[0], ReachabilityState.UP),
        NodeHealth(nodes[1], ReachabilityState.UP),
        NodeHealth(nodes[2], ReachabilityState.DOWN),
        NodeHealth(nodes[3], ReachabilityState.DOWN),
    ]
    snap = build_snapshot(
        timestamp=datetime.now(UTC),
        cfg=cfg,
        self_node_id="n1",
        node_health=nhs,
    )
    assert snap.overall == OverallHealth.DEGRADED
