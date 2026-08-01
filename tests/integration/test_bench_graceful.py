"""Bench without iperf3."""

from __future__ import annotations

import pytest

from maccluster.adapters.iperf3 import FakeBench
from maccluster.errors import CliError
from maccluster.services.bench_service import run_bench


def test_graceful(fake_ctx):
    fake_ctx.bench = FakeBench(available=False)
    with pytest.raises(CliError) as ei:
        run_bench(fake_ctx, "10.42.0.2")
    assert ei.value.exit_code == 1
