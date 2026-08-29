"""`maccluster status` shows per peer transport=<rdma|tb|wifi|unknown>."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from maccluster.render.plain import render_status
from maccluster.services.status_service import collect_status, derive_peer_transport


@pytest.fixture
def arep_status(fixtures_dir: Path) -> dict:
    return json.loads((fixtures_dir / "arep" / "status_node_a.json").read_text(encoding="utf-8"))


def test_derive_prefers_maccluster_sync_record_over_arep():
    transport, source, detail = derive_peer_transport(
        {"transportCapable": ["rdma", "tcp"], "lastTransport": "rdma"},
        {"transport": "wifi", "downgrades": ["transport downgrade tb→wifi: ssh timeout"]},
        bridge_reachable=True,
    )
    assert (transport, source) == ("wifi", "sync-last")
    assert "tb→wifi" in detail


def test_derive_from_arep_last_transport():
    assert derive_peer_transport({"lastTransport": "rdma"}, None, bridge_reachable=True)[:2] == (
        "rdma",
        "arep",
    )
    # arep's tcp channel binds to bridge0 when it can; otherwise it went another way.
    t, s, d = derive_peer_transport(
        {"lastTransport": "tcp", "lastDowngradeReason": "link-lost"}, None, bridge_reachable=True
    )
    assert (t, s) == ("tb", "arep") and "link-lost" in d
    assert derive_peer_transport({"lastTransport": "tcp"}, None, bridge_reachable=False)[0] == (
        "wifi"
    )


def test_derive_from_capability_only_and_unknown():
    t, s, d = derive_peer_transport(
        {"transportCapable": ["rdma", "tcp"]}, None, bridge_reachable=True
    )
    assert (t, s) == ("rdma", "arep") and "capable" in d
    assert derive_peer_transport(None, None, bridge_reachable=True)[0] == "unknown"
    assert (
        derive_peer_transport({"lastTransport": "bogus\x1b[31m"}, None, bridge_reachable=True)[0]
        == "unknown"
    )
    # sync-last with an empty transport (nothing ran) does not shadow arep
    assert (
        derive_peer_transport({"lastTransport": "rdma"}, {"transport": ""}, bridge_reachable=True)[
            0
        ]
        == "rdma"
    )


def test_collect_status_sets_transport_per_peer(fake_ctx, arep_status):
    last = {
        "peers": [
            {
                "peer_id": "node-c",
                "transport": "wifi",
                "downgrades": ["transport downgrade tb→wifi: x"],
            }
        ]
    }
    snap, _ = collect_status(fake_ctx, arep_status=lambda: arep_status, last_sync=lambda: last)
    by_id = {nh.node.id: nh for nh in snap.nodes}
    assert by_id["node-a"].transport == "self"
    assert by_id["node-b"].transport == "rdma" and by_id["node-b"].transport_source == "arep"
    assert by_id["node-c"].transport == "wifi" and by_id["node-c"].transport_source == "sync-last"
    assert by_id["node-d"].transport == "unknown"


def test_collect_status_survives_broken_sources(fake_ctx):
    def boom():
        raise RuntimeError("no arep")

    snap, _ = collect_status(fake_ctx, arep_status=boom, last_sync=boom)
    assert all(nh.transport in ("self", "unknown") for nh in snap.nodes)


def test_render_status_shows_transport(fake_ctx, arep_status):
    snap, _ = collect_status(fake_ctx, arep_status=lambda: arep_status, last_sync=lambda: None)
    text = render_status(snap)
    line_b = next(ln for ln in text.splitlines() if ln.lstrip("* ").startswith("node-b"))
    line_c = next(ln for ln in text.splitlines() if ln.lstrip("* ").startswith("node-c"))
    assert "transport=rdma" in line_b
    assert "transport=tb" in line_c
    assert "link-lost" in text
    line_a = next(ln for ln in text.splitlines() if ln.lstrip("* ").startswith("node-a"))
    assert "transport=" not in line_a
