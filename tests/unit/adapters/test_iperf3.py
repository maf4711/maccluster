"""iperf3 adapter helpers."""

from __future__ import annotations

from maccluster.adapters.iperf3 import FakeBench, _parse_iperf_json
from maccluster.domain.enums import BenchQuality


def test_parse_json():
    text = '{"end":{"sum_sent":{"bits_per_second": 2500000000,"retransmits":2}}}'
    mbps, retrans = _parse_iperf_json(text)
    assert mbps == 2500.0
    assert retrans == 2


def test_fake_missing():
    b = FakeBench(available=False)
    r = b.run("10.42.0.2")
    assert not r.success
    assert "iperf3 not found" in r.message


def test_fake_bench_quality():
    b = FakeBench(mbps=35_000.0, retransmits=0)
    r = b.run("10.42.0.2")
    assert r.success
    assert r.quality == BenchQuality.EXCELLENT
