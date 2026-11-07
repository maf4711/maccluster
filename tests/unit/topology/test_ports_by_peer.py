"""Per-peer TB port attribution (bugfix: every peer showed the machine-best link)."""

from __future__ import annotations

from ipaddress import IPv4Address

from maccluster.domain.enums import LinkState
from maccluster.domain.models import Node, ThunderboltPort, ThunderboltSnapshot
from maccluster.topology.match import best_link_speed, ports_by_peer


def _port(
    receptacle: str,
    gbps: float | None,
    state: LinkState = LinkState.CONNECTED,
    *,
    peer_name: str | None = "Mac16,11",
    peer_domain_uuid: str | None = None,
    peer_uid: str | None = None,
) -> ThunderboltPort:
    return ThunderboltPort(
        receptacle_id=receptacle,
        interface_name=None,
        capable=True,
        thunderbolt_version="USB4",
        link_speed_gbps=gbps,
        link_state=state,
        peer_name=peer_name,
        peer_domain_uuid=peer_domain_uuid,
        peer_uid=peer_uid,
    )


def _node(node_id: str, last_octet: int, **kw) -> Node:
    return Node(
        id=node_id,
        hostnames=(f"{node_id}.local", node_id),
        ip=IPv4Address(f"10.42.0.{last_octet}"),
        hw_uuid=f"00000000-0000-0000-0000-00000000000{last_octet}",
        **kw,
    )


NODE_B = _node("node-b", 2, tb_domain_uuids=("BBBBBBBB-1111-2222-3333-444444444444",))
NODE_C = _node("node-c", 3, tb_controller_uids=("0xCCC0001",))


def test_two_peer_ports_map_to_their_own_link() -> None:
    tb = ThunderboltSnapshot(
        ports=(
            _port("1", 80.0, peer_domain_uuid="BBBBBBBB-1111-2222-3333-444444444444"),
            _port("2", 40.0, peer_uid="0xCCC0001"),
            _port("3", 120.0, LinkState.UNCONNECTED, peer_name=None),
        ),
        source="fake",
    )
    mapped = ports_by_peer(tb=tb, peers=(NODE_B, NODE_C))
    assert best_link_speed(mapped["node-b"]) == 80.0
    assert best_link_speed(mapped["node-c"]) == 40.0


def test_ambiguous_ports_are_not_attributed() -> None:
    # Two identical peers, no identity data: never guess, never hand out max().
    tb = ThunderboltSnapshot(
        ports=(_port("1", 80.0), _port("2", 40.0)),
        source="fake",
    )
    assert ports_by_peer(tb=tb, peers=(NODE_B, NODE_C)) == {}


def test_single_peer_single_mac_port_fallback() -> None:
    # One configured peer and exactly one connected Mac port is unambiguous
    # even without controller UID / domain UUID identity.
    tb = ThunderboltSnapshot(
        ports=(
            _port("1", 40.0),
            _port("2", None, LinkState.UNCONNECTED, peer_name=None),
        ),
        source="fake",
    )
    mapped = ports_by_peer(tb=tb, peers=(NODE_B,))
    assert best_link_speed(mapped["node-b"]) == 40.0


def test_single_peer_two_mac_ports_stays_unattributed() -> None:
    tb = ThunderboltSnapshot(
        ports=(_port("1", 80.0), _port("2", 40.0)),
        source="fake",
    )
    # Could be a display chain or a mis-scanned second Mac: don't guess.
    assert ports_by_peer(tb=tb, peers=(NODE_B,)) == {}


def test_no_snapshot_yields_empty_mapping() -> None:
    assert ports_by_peer(tb=None, peers=(NODE_B, NODE_C)) == {}
    assert best_link_speed(()) is None
