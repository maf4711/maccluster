"""bench missing iperf."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from maccluster.adapters.iperf3 import FakeBench
from maccluster.commands import bench
from maccluster.errors import CliError


def test_missing(fake_ctx):
    fake_ctx.bench = FakeBench(available=False)
    with pytest.raises(CliError) as ei:
        bench.run(fake_ctx, SimpleNamespace(target="10.42.0.2", duration=1))
    assert ei.value.exit_code == 1
