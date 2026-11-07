"""Thunderbolt identities from ``system_profiler SPThunderboltDataType -json``.

Fixture ``node_a_macos27_2026-08-29.json`` is a real capture from node-a
(Mac mini, macOS 27.0): bus_1 → MacBook Pro peer (Mac17,6), bus_2 → Studio
Display, bus_0 idle. Domain UUIDs regenerate on every reboot; the
``switch_uid_key`` per bus (0x05AC51E771159CF0..F2) is the stable controller UID.
"""

from __future__ import annotations

from ipaddress import IPv4Address
from pathlib import Path

import pytest

from maccluster.adapters.tb_system_profiler import parse_system_profiler_tb
from maccluster.domain.enums import CheckSeverity, LinkState
from maccluster.domain.models import Node, ThunderboltPort, ThunderboltSnapshot
from maccluster.mapping.tb_identity import (
    check_tb_identity,
    controller_uid_from_node_guid,
    live_controller_uids,
    live_domain_uuids,
    normalize_uid,
    normalize_uuid,
    parse_system_profiler_json,
)

NODE_A_UIDS = ("0x05AC51E771159CF0", "0x05AC51E771159CF1", "0x05AC51E771159CF2")
NODE_A_LIVE_UUIDS = (
    "3C9311A2-3DFC-44C4-AEC3-81086B2880BB",
    "4FBE0A88-3F0B-46F4-9511-3713B2315360",
    "DFD77E42-7ED0-48E5-A840-94FD1522F505",
)
# What ~/.config/maccluster/cluster.toml still carries for node-a (pre-reboot).
NODE_A_STALE_UUIDS = (
    "676DF3C0-A43A-4D60-8154-6246AF7FBF00",
    "E9F38DFF-9A9A-4A0A-8D9C-02C3325633C0",
    "2D9DB209-A8AE-4EC6-B7F5-38F5960A04C5",
)


@pytest.fixture
def node_a_json(fixtures_dir: Path) -> str:
    return (fixtures_dir / "system_profiler" / "node_a_macos27_2026-08-29.json").read_text(
        encoding="utf-8"
    )


@pytest.fixture
def node_a_text(fixtures_dir: Path) -> str:
    return (fixtures_dir / "system_profiler" / "node_a_macos27_2026-08-29.txt").read_text(
        encoding="utf-8"
    )


def _port(snap: ThunderboltSnapshot, receptacle: str) -> ThunderboltPort:
    return next(p for p in snap.ports if p.receptacle_id == receptacle)


def test_json_parse_yields_one_port_per_bus_sorted_by_receptacle(node_a_json):
    snap = parse_system_profiler_json(node_a_json)
    assert snap.source == "system_profiler-json"
    assert snap.host_model == "Mac mini"
    assert [p.receptacle_id for p in snap.ports] == ["1", "2", "3"]
    assert [p.bus_uid for p in snap.ports] == list(NODE_A_UIDS)
    assert [p.domain_uuid for p in snap.ports] == list(NODE_A_LIVE_UUIDS)


def test_json_parse_peer_mac_carries_domain_but_no_uid(node_a_json):
    p2 = _port(parse_system_profiler_json(node_a_json), "2")
    assert p2.link_state == LinkState.CONNECTED
    assert p2.link_speed_gbps == 40.0
    assert p2.peer_name == "Mac17,6"
    assert p2.peer_domain_uuid == "BC5DEC53-7E36-4A9A-8459-456EBAB5E58A"
    # macOS 27.0 does not expose a peer Mac's controller UID locally.
    assert p2.peer_uid is None


def test_json_parse_display_carries_switch_uid(node_a_json):
    p3 = _port(parse_system_profiler_json(node_a_json), "3")
    assert p3.peer_name == "Studio Display"
    assert p3.peer_uid == "0x000196C394A8D900"
    assert p3.peer_domain_uuid is None
    assert p3.bus_uid == "0x05AC51E771159CF2"  # nested UID must not clobber the bus UID


def test_json_parse_idle_port(node_a_json):
    p1 = _port(parse_system_profiler_json(node_a_json), "1")
    assert p1.link_state == LinkState.UNCONNECTED
    assert p1.peer_name is None and p1.peer_uid is None and p1.peer_domain_uuid is None
    assert p1.link_speed_gbps == 120.0


def test_json_and_text_parsers_agree_on_identities(node_a_json, node_a_text):
    from_json = parse_system_profiler_json(node_a_json)
    from_text = parse_system_profiler_tb(node_a_text)
    key = lambda p: (p.receptacle_id, p.bus_uid, p.domain_uuid, p.peer_domain_uuid, p.peer_uid)  # noqa: E731
    assert sorted(key(p) for p in from_json.ports) == sorted(key(p) for p in from_text.ports)


def test_json_parse_rejects_garbage():
    assert parse_system_profiler_json("not json").ports == ()
    assert parse_system_profiler_json('{"SPThunderboltDataType": "x"}').ports == ()
    assert parse_system_profiler_json("[]").ports == ()


def test_normalize_uid_accepts_hex_and_ioreg_decimal():
    assert normalize_uid("0x05ac51e771159cf0") == "0x05AC51E771159CF0"
    assert normalize_uid(" 0X05AC51E771159CF0 ") == "0x05AC51E771159CF0"
    assert normalize_uid(408791720660409584) == "0x05AC51E771159CF0"  # ioreg "UID" = decimal
    assert normalize_uid("408791720660409584") == "0x05AC51E771159CF0"
    assert normalize_uid("") is None
    assert normalize_uid(None) is None
    assert normalize_uid("zz") is None


def test_normalize_uuid():
    assert normalize_uuid("bc5dec53-7e36-4a9a-8459-456ebab5e58a") == (
        "BC5DEC53-7E36-4A9A-8459-456EBAB5E58A"
    )
    assert normalize_uuid("  ") is None
    assert normalize_uuid("not-a-uuid") is None


def test_controller_uid_from_rdma_node_guid():
    # ioreg AppleThunderboltRDMAInterface rdma_en2 node_guid is the byte-reversed bus UID.
    assert controller_uid_from_node_guid("f09c:1571:e751:ac05") == "0x05AC51E771159CF0"
    assert controller_uid_from_node_guid("f29c:1571:e751:ac05") == "0x05AC51E771159CF2"
    assert controller_uid_from_node_guid("nope") is None


def test_live_ids_from_snapshot(node_a_json):
    snap = parse_system_profiler_json(node_a_json)
    assert live_domain_uuids(snap) == NODE_A_LIVE_UUIDS
    assert live_controller_uids(snap) == NODE_A_UIDS


def _node(uuids=(), uids=()) -> Node:
    return Node(
        id="node-a",
        hostnames=("mac-mini-a",),
        ip=IPv4Address("10.42.0.1"),
        hw_uuid="00000000-0000-0000-0000-000000000001",
        tb_domain_uuids=tuple(uuids),
        tb_controller_uids=tuple(uids),
    )


def test_check_tb_identity_warns_stale_when_uids_confirm_this_mac(node_a_json):
    snap = parse_system_profiler_json(node_a_json)
    f = check_tb_identity(_node(NODE_A_STALE_UUIDS, ("0x05ac51e771159cf0",)), snap)
    assert f.check_id == "tb_ids"
    assert f.severity == CheckSeverity.WARN
    assert f.summary == "tb_domain_uuids stale"
    assert "refresh-tb" in f.detail


def test_check_tb_identity_ok_when_a_live_uuid_matches(node_a_json):
    snap = parse_system_profiler_json(node_a_json)
    f = check_tb_identity(_node(("x", NODE_A_LIVE_UUIDS[1].lower()), ()), snap)
    assert f.severity == CheckSeverity.OK


def test_check_tb_identity_info_when_nothing_confirms(node_a_json):
    snap = parse_system_profiler_json(node_a_json)
    f = check_tb_identity(_node(NODE_A_STALE_UUIDS, ()), snap)
    assert f.severity == CheckSeverity.INFO
    assert "tb_controller_uids" in f.detail  # advice: pin the UIDs so doctor can tell
    assert check_tb_identity(_node((), ()), snap).severity == CheckSeverity.INFO
    assert check_tb_identity(None, snap).severity == CheckSeverity.INFO
    assert check_tb_identity(_node(NODE_A_STALE_UUIDS, ()), None).severity == CheckSeverity.INFO
