"""Peer matching prefers stable controller UIDs over per-boot domain UUIDs."""

from __future__ import annotations

from ipaddress import IPv4Address, IPv4Network

from maccluster.config.load import load_toml_text
from maccluster.domain.enums import LinkState
from maccluster.domain.models import ClusterConfig, Node, ThunderboltPort, ThunderboltSnapshot
from maccluster.mapping.peer_match import PeerMatch, match_node
from maccluster.topology.build import build_topology
from maccluster.topology.match import match_peer

NODE_B = Node(
    id="node-b",
    hostnames=("mini-b.local",),
    ip=IPv4Address("10.42.0.2"),
    hw_uuid="409C591A-0000-0000-0000-000000000002",
    tb_domain_uuids=("E4CCB4B9-724D-4728-ADE2-F356148F8F79",),
    tb_controller_uids=("0x05AC20D5BAF28990", "0x05AC20D5BAF28991"),
)
NODE_C = Node(
    id="node-c",
    hostnames=("mbp-c.local",),
    ip=IPv4Address("10.42.0.3"),
    hw_uuid="409C591A-0000-0000-0000-000000000003",
    tb_domain_uuids=("9EFBA377-528E-4A87-B974-913EE77BCB9A",),
    tb_controller_uids=("0x05AC0B4F1F5E2C60",),
)
NODES = (NODE_B, NODE_C)


def test_uid_wins_over_conflicting_domain_uuid():
    # Domain says node-c (stale config), controller UID says node-b: UID is stable.
    got = match_node(
        nodes=NODES,
        peer_uid="0x05ac20d5baf28991",
        peer_domain_uuid="9EFBA377-528E-4A87-B974-913EE77BCB9A",
        peer_hint="Mac16,11",
    )
    assert got == PeerMatch(node_id="node-b", by="uid")


def test_domain_uuid_then_hostname_fallback():
    assert match_node(
        nodes=NODES, peer_uid=None, peer_domain_uuid="e4ccb4b9-724d-4728-ade2-f356148f8f79"
    ) == PeerMatch("node-b", "domain")
    assert match_node(nodes=NODES, peer_hint="mbp-c") == PeerMatch("node-c", "hostname")
    assert match_node(nodes=NODES, peer_hint="Mac16,11") is None
    assert match_node(nodes=NODES) is None


def test_uid_matches_ioreg_decimal_form():
    assert match_node(nodes=NODES, peer_uid="408719720404942176") is None  # unrelated
    assert match_node(nodes=NODES, peer_uid=int("0x05AC0B4F1F5E2C60", 16)) == PeerMatch(
        "node-c", "uid"
    )


def test_topology_match_peer_keeps_signature_and_accepts_uid():
    assert match_peer(peer_hint=None, peer_domain_uuid=None, nodes=NODES) is None
    assert (
        match_peer(
            peer_hint="Mac16,11",
            peer_domain_uuid=None,
            peer_uid="0x05AC0B4F1F5E2C60",
            nodes=NODES,
        )
        == "node-c"
    )


def test_config_loads_optional_tb_controller_uids():
    cfg = load_toml_text(
        """
schema_version = 1
name = "t"
subnet = "10.42.0.0/24"

[[nodes]]
id = "node-a"
hostnames = ["a.local"]
ip = "10.42.0.1"
hw_uuid = "409C591A-0000-0000-0000-000000000001"
tb_domain_uuids = ["E9F38DFF-9A9A-4A0A-8D9C-02C3325633C0"]
tb_controller_uids = ["0x05AC51E771159CF0", " 0x05AC51E771159CF1 ", ""]

[[nodes]]
id = "node-b"
hostnames = ["b.local"]
ip = "10.42.0.2"
hw_uuid = "409C591A-0000-0000-0000-000000000002"
"""
    )
    a, b = cfg.nodes
    assert a.tb_controller_uids == ("0x05AC51E771159CF0", "0x05AC51E771159CF1")
    assert b.tb_controller_uids == ()
    assert a.with_role(a.role).tb_controller_uids == a.tb_controller_uids


def _tb(peer_uid: str | None, peer_domain: str | None) -> ThunderboltSnapshot:
    return ThunderboltSnapshot(
        ports=(
            ThunderboltPort(
                receptacle_id="2",
                interface_name=None,
                capable=True,
                thunderbolt_version="USB4",
                link_speed_gbps=40.0,
                link_state=LinkState.CONNECTED,
                domain_uuid="4FBE0A88-3F0B-46F4-9511-3713B2315360",
                peer_name="Mac16,11",
                bus_uid="0x05AC51E771159CF1",
                peer_domain_uuid=peer_domain,
                peer_uid=peer_uid,
            ),
        ),
        source="fake",
    )


def test_build_topology_matches_by_uid_when_domain_uuids_are_stale():
    cfg = ClusterConfig(
        schema_version=1,
        name="t",
        subnet=IPv4Network("10.42.0.0/24"),
        bridge_interface="bridge0",
        nodes=NODES,
    )
    # Peer rebooted: its domain UUID is new (matches nothing), its UID is unchanged.
    topo = build_topology(
        cfg=cfg,
        tb=_tb("0x05AC20D5BAF28990", "11111111-2222-3333-4444-555555555555"),
        self_node=None,
    )
    link = topo.links[0]
    assert link.matched_node_id == "node-b"
    assert link.matched_by == "uid"
    assert link.peer_uid == "0x05AC20D5BAF28990"
    assert link.peer_domain_uuid == "11111111-2222-3333-4444-555555555555"
    assert topo.unmatched_peers == ()

    # No UID visible (a peer Mac on macOS 27.0) and stale UUID → still unmatched.
    stale = build_topology(
        cfg=cfg, tb=_tb(None, "11111111-2222-3333-4444-555555555555"), self_node=None
    )
    assert stale.links[0].matched_node_id is None
    assert stale.links[0].matched_by is None
    assert stale.unmatched_peers == ("Mac16,11",)
