"""Text parser: a nested device's UID is the peer's, never the bus UID."""

from __future__ import annotations

from pathlib import Path

from maccluster.adapters.tb_system_profiler import parse_system_profiler_tb
from maccluster.render.plain import render_topo
from maccluster.topology.build import build_topology


def test_nested_uid_becomes_peer_uid_and_bus_uid_survives(fixtures_dir: Path):
    text = (fixtures_dir / "system_profiler" / "node_a_macos27_2026-08-29.txt").read_text()
    snap = parse_system_profiler_tb(text)
    by_rec = {p.receptacle_id: p for p in snap.ports}
    assert by_rec["3"].bus_uid == "0x05AC51E771159CF2"
    assert by_rec["3"].peer_uid == "0x000196C394A8D900"
    assert by_rec["3"].peer_name == "Studio Display"
    assert by_rec["2"].bus_uid == "0x05AC51E771159CF1"
    assert by_rec["2"].peer_uid is None
    assert by_rec["2"].peer_domain_uuid == "BC5DEC53-7E36-4A9A-8459-456EBAB5E58A"
    assert by_rec["1"].peer_uid is None


def test_render_topo_shows_peer_ids(fixtures_dir: Path, fake_ctx):
    from maccluster.services.config_service import load_config

    text = (fixtures_dir / "system_profiler" / "node_a_macos27_2026-08-29.txt").read_text()
    topo = build_topology(
        cfg=load_config(fake_ctx), tb=parse_system_profiler_tb(text), self_node=None
    )
    out = render_topo(topo)
    assert "peer_domain=BC5DEC53-7E36-4A9A-8459-456EBAB5E58A" in out
    assert "peer_uid=0x000196C394A8D900" in out
    unmatched = next(ln for ln in out.splitlines() if ln.startswith("unmatched peers:"))
    assert "Mac17,6" in unmatched and "Studio Display" in unmatched
    assert "matched=-" in out and " by=" not in out
