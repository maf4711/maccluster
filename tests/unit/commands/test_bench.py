"""bench command."""

from __future__ import annotations

from types import SimpleNamespace

from maccluster.commands import bench


def test_bench_cmd(fake_ctx, capsys):
    code = bench.run(fake_ctx, SimpleNamespace(target="10.42.0.2", duration=1))
    assert code == 0
    assert "Mbit" in capsys.readouterr().out
