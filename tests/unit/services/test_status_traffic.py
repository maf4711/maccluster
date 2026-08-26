"""Status includes interface traffic rates after two samples."""

from __future__ import annotations

from maccluster.health.traffic import TrafficSampler
from maccluster.services.status_service import collect_status, get_traffic_sampler


def test_status_traffic_rates_second_sample(fake_ctx, tmp_path):
    get_traffic_sampler(reset=True)
    sampler = TrafficSampler(use_disk_cache=True, cache_path=tmp_path / "t.json")

    snap1, _ = collect_status(fake_ctx, sampler=sampler, persist_traffic=True)
    assert snap1.traffic  # at least bridge0 from fake
    assert all(not t.rate_available for t in snap1.traffic)

    snap2, _ = collect_status(fake_ctx, sampler=sampler, persist_traffic=True)
    assert snap2.traffic
    # FakeNetworkRead advances counters each call → rates on 2nd sample
    assert any(t.rate_available for t in snap2.traffic)
    rated = next(t for t in snap2.traffic if t.rate_available)
    assert rated.rx_bps is not None and rated.rx_bps > 0
    assert rated.tx_bps is not None and rated.tx_bps > 0


def test_render_status_includes_traffic(fake_ctx, tmp_path):
    from maccluster.render.plain import render_status

    sampler = TrafficSampler(use_disk_cache=False, cache_path=tmp_path / "x.json")
    collect_status(fake_ctx, sampler=sampler)
    snap, _ = collect_status(fake_ctx, sampler=sampler)
    text = render_status(snap)
    assert "traffic" in text
    assert "RX" in text and "TX" in text
