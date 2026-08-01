"""iperf3 adapter helpers."""

from __future__ import annotations

from maccluster.adapters.iperf3 import FakeBench, _parse_iperf_json


def test_parse_json():
    text = '{"end":{"sum_sent":{"bits_per_second": 2500000000}}}'
    assert _parse_iperf_json(text) == 2500.0


def test_fake_missing():
    b = FakeBench(available=False)
    r = b.run("10.42.0.2")
    assert not r.success
    assert "iperf3 not found" in r.message
