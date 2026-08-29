"""Peer matching via Thunderbolt domain UUID with hostname fallback."""

from __future__ import annotations

from ipaddress import IPv4Address

from maccluster.domain.enums import LinkState
from maccluster.domain.models import ClusterConfig, Node, ThunderboltPort, ThunderboltSnapshot
from maccluster.topology.build import build_topology
from maccluster.topology.match import match_peer

NODE_A = Node(
    id="node-a",
    hostnames=("mini-a.local",),
    ip=IPv4Address("10.42.0.1"),
    hw_uuid="409C591A-0000-0000-0000-000000000001",
    tb_domain_uuids=("E9F38DFF-9A9A-4A0A-8D9C-02C3325633C0",),
)
NODE_B = Node(
    id="node-b",
    hostnames=("mini-b.local",),
    ip=IPv4Address("10.42.0.2"),
    hw_uuid="409C591A-0000-0000-0000-000000000002",
    tb_domain_uuids=("E4CCB4B9-724D-4728-ADE2-F356148F8F79",),
)


def test_match_peer_prefers_domain_uuid_over_useless_model_name():
    assert (
        match_peer(
            peer_hint="Mac16,11",
            peer_domain_uuid="e4ccb4b9-724d-4728-ade2-f356148f8f79",
            nodes=(NODE_A, NODE_B),
        )
        == "node-b"
    )


def test_match_peer_falls_back_to_hostname():
    assert match_peer(peer_hint="mini-a", peer_domain_uuid=None, nodes=(NODE_A, NODE_B)) == "node-a"


def test_build_topology_matches_links_by_peer_domain_uuid():
    cfg = ClusterConfig(
        schema_version=1,
        name="t",
        subnet=__import__("ipaddress").IPv4Network("10.42.0.0/24"),
        bridge_interface="bridge0",
        nodes=(NODE_A, NODE_B),
    )
    tb = ThunderboltSnapshot(
        ports=(
            ThunderboltPort(
                receptacle_id="2",
                interface_name=None,
                capable=True,
                thunderbolt_version="USB4",
                link_speed_gbps=40.0,
                link_state=LinkState.CONNECTED,
                domain_uuid="68947458-9A96-4930-9E4F-9D614759AE6E",
                peer_name="Mac16,11",
                peer_domain_uuid="E9F38DFF-9A9A-4A0A-8D9C-02C3325633C0",
            ),
        ),
        source="fake",
        host_model="MacBook Pro",
    )
    topo = build_topology(cfg=cfg, tb=tb, self_node=None)
    assert topo.links[0].matched_node_id == "node-a"
    assert topo.unmatched_peers == ()
