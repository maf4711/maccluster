"""Bench path-quality flags."""

from __future__ import annotations

from maccluster.domain.enums import BenchQuality
from maccluster.health.bench_quality import assess_bench_quality


def test_excellent_tb_class():
    q, flags = assess_bench_quality(35_000.0, retransmits=0)
    assert q == BenchQuality.EXCELLENT
    assert flags == ()


def test_retransmits_flag():
    q, flags = assess_bench_quality(32_000.0, retransmits=3)
    assert q == BenchQuality.EXCELLENT
    assert "retransmits=3" in flags


def test_poor_low_throughput():
    q, flags = assess_bench_quality(50.0)
    assert q == BenchQuality.POOR
    assert any("very_low" in f for f in flags)
