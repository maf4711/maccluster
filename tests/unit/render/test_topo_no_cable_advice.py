"""A-023: topo output must not recommend physical rewiring."""

from __future__ import annotations

from ipaddress import IPv4Address, IPv4Network

from maccluster.domain.enums import LinkState, ReachabilityState
from maccluster.domain.models import ClusterConfig, Node, ThunderboltPort, ThunderboltSnapshot
from maccluster.render.plain import render_topo
from maccluster.topology.build import build_topology


def test_render_topo_no_plug_recommendation():
    nodes = (
        Node("node-a", ("a",), IPv4Address("10.42.0.1"), "u1"),
        Node("node-b", ("b",), IPv4Address("10.42.0.2"), "u2"),
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
        source="fixture",
    )
    topo = build_topology(
        cfg=cfg,
        tb=tb,
        self_node=nodes[0],
        reachability={"10.42.0.2": ReachabilityState.DOWN},
    )
    out = render_topo(topo).lower()
    forbidden = (
        "plug cable from",
        "plug cable",
        "rewire",
        "move cable",
        "connect cable from",
        "empfehlung",
    )
    for phrase in forbidden:
        assert phrase not in out, f"unexpected advice phrase: {phrase!r}"
    assert "topology" in out
    assert "receptacle" in out
