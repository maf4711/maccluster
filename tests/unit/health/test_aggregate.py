"""Aggregate exit codes."""

from __future__ import annotations

from datetime import UTC, datetime
from ipaddress import IPv4Address

from maccluster.domain.enums import OverallHealth, ReachabilityState
from maccluster.domain.models import HealthSnapshot, Node, NodeHealth
from maccluster.health.aggregate import exit_code_for_snapshot


def test_exit_mapping():
    node = Node("a", ("a",), IPv4Address("10.42.0.1"), "u")
    base = dict(
        timestamp=datetime.now(UTC),
        cluster_name="c",
        self_node_id="a",
        nodes=(NodeHealth(node, ReachabilityState.UP),),
        bridge=None,
        tb=None,
    )
    assert exit_code_for_snapshot(HealthSnapshot(**base, overall=OverallHealth.HEALTHY)) == 0
    assert exit_code_for_snapshot(HealthSnapshot(**base, overall=OverallHealth.DEGRADED)) == 3
    assert exit_code_for_snapshot(HealthSnapshot(**base, overall=OverallHealth.UNHEALTHY)) == 1
