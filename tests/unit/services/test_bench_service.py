"""Bench service."""

from __future__ import annotations

import pytest

from maccluster.adapters.iperf3 import FakeBench
from maccluster.errors import CliError
from maccluster.services.bench_service import run_bench


def test_bench_ok(fake_ctx):
    r = run_bench(fake_ctx, "10.42.0.2")
    assert r.success
    assert r.mbps == 1000.0


def test_bench_missing_iperf(fake_ctx):
    fake_ctx.bench = FakeBench(available=False)
    with pytest.raises(CliError) as ei:
        run_bench(fake_ctx, "10.42.0.2")
    assert ei.value.exit_code == 1
    assert "iperf3 not found" in ei.value.message


def test_bench_requires_target(fake_ctx):
    with pytest.raises(CliError) as ei:
        run_bench(fake_ctx, None)
    assert ei.value.exit_code == 2
