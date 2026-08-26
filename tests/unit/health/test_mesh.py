"""Fabric mesh health verdicts."""

from __future__ import annotations

from ipaddress import IPv4Address

from maccluster.domain.enums import MeshVerdict, ReachabilityState
from maccluster.domain.models import BridgeInterface, Node, NodeHealth
from maccluster.health.mesh import build_mesh_health


def _nh(node_id: str, ip: str, state: ReachabilityState) -> NodeHealth:
    return NodeHealth(
        node=Node(node_id, (node_id,), IPv4Address(ip), "u"),
        reachability=state,
    )


def test_mesh_ok():
    nodes = [
        _nh("a", "10.42.0.1", ReachabilityState.UP),
        _nh("b", "10.42.0.2", ReachabilityState.UP),
        _nh("c", "10.42.0.3", ReachabilityState.UP),
    ]
    bridge = BridgeInterface(
        name="bridge0", exists=True, admin_up=True, addresses=(IPv4Address("10.42.0.1"),)
    )
    m = build_mesh_health(nodes, self_node_id="a", bridge=bridge)
    assert m.verdict == MeshVerdict.OK
    assert m.fully_meshed
    assert m.peers_up == 2
    assert m.bridge_ok


def test_mesh_partial():
    nodes = [
        _nh("a", "10.42.0.1", ReachabilityState.UP),
        _nh("b", "10.42.0.2", ReachabilityState.UP),
        _nh("c", "10.42.0.3", ReachabilityState.DOWN),
    ]
    m = build_mesh_health(nodes, self_node_id="a")
    assert m.verdict == MeshVerdict.PARTIAL
    assert m.peers_up == 1
    assert m.peers_down == 1
    assert not m.fully_meshed


def test_mesh_isolated():
    nodes = [
        _nh("a", "10.42.0.1", ReachabilityState.UP),
        _nh("b", "10.42.0.2", ReachabilityState.DOWN),
    ]
    m = build_mesh_health(nodes, self_node_id="a")
    assert m.verdict == MeshVerdict.ISOLATED
    assert "isolated" in m.summary
