"""Topology build."""

from __future__ import annotations

from ipaddress import IPv4Address, IPv4Network

from maccluster.domain.enums import LinkState, ReachabilityState
from maccluster.domain.models import ClusterConfig, Node, ThunderboltPort, ThunderboltSnapshot
from maccluster.topology.build import build_topology


def test_build_unconnected():
    nodes = (
        Node("a", ("a",), IPv4Address("10.42.0.1"), "u1"),
        Node("b", ("b",), IPv4Address("10.42.0.2"), "u2"),
    )
    cfg = ClusterConfig(1, "c", IPv4Network("10.42.0.0/24"), "bridge0", nodes)
    tb = ThunderboltSnapshot(
        ports=(
            ThunderboltPort(
                "1",
                None,
                True,
                "USB4",
                40.0,
                LinkState.UNCONNECTED,
                domain_uuid="D1",
            ),
        ),
        source="fake",
    )
    topo = build_topology(
        cfg=cfg,
        tb=tb,
        self_node=nodes[0],
        reachability={"10.42.0.2": ReachabilityState.UP},
    )
    assert len(topo.links) == 1
    assert "plug cable" not in str(topo).lower()
