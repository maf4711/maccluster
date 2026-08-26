"""Path-quality classification for iperf3 results over the TB fabric."""

from __future__ import annotations

from maccluster.constants import (
    BENCH_EXCELLENT_MBPS,
    BENCH_GOOD_MBPS,
    BENCH_MARGINAL_MBPS,
)
from maccluster.domain.enums import BenchQuality


def assess_bench_quality(
    mbps: float | None,
    *,
    retransmits: int | None = None,
) -> tuple[BenchQuality, tuple[str, ...]]:
    flags: list[str] = []
    if mbps is None:
        return BenchQuality.UNKNOWN, ()

    if retransmits is not None and retransmits > 0:
        flags.append(f"retransmits={retransmits}")

    if mbps >= BENCH_EXCELLENT_MBPS:
        quality = BenchQuality.EXCELLENT
    elif mbps >= BENCH_GOOD_MBPS:
        quality = BenchQuality.GOOD
        if mbps < BENCH_EXCELLENT_MBPS * 0.5:
            flags.append(f"below_tb_ideal<{BENCH_EXCELLENT_MBPS:g}Mbps")
    elif mbps >= BENCH_MARGINAL_MBPS:
        quality = BenchQuality.MARGINAL
        flags.append(f"low_throughput={mbps:.0f}Mbps")
    else:
        quality = BenchQuality.POOR
        flags.append(f"very_low_throughput={mbps:.0f}Mbps")

    return quality, tuple(flags)
