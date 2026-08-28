"""CLI: bench --mesh vs single-target."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from maccluster.adapters.iperf3 import FakeBench
from maccluster.cli.exit_codes import DEGRADED, USAGE
from maccluster.cli.parser import build_parser
from maccluster.commands import bench
from maccluster.errors import CliError


def test_parse_mesh_flags():
    p = build_parser()
    args = p.parse_args(["bench", "--mesh", "--peer", "node-b", "--force", "--duration", "3"])
    assert args.mesh is True
    assert args.peer == "node-b"
    assert args.force is True
    assert args.duration == 3
    assert args.target is None


def test_mesh_and_positional_target_is_usage(fake_ctx):
    from maccluster.services.mesh_bench_service import reject_mesh_target_combo

    with pytest.raises(CliError) as ei:
        reject_mesh_target_combo(mesh=True, target="10.42.0.2")
    assert ei.value.exit_code == USAGE
    with pytest.raises(CliError) as ei2:
        bench.run(
            fake_ctx,
            SimpleNamespace(
                target="10.42.0.2",
                mesh=True,
                peer=None,
                force=False,
                duration=1,
            ),
        )
    assert ei2.value.exit_code == USAGE


def test_mesh_busy_exit_degraded(fake_ctx, monkeypatch, capsys):
    fake_ctx.bench = FakeBench()

    def _no_ssh(basename: str) -> str:
        raise CliError("tool not found", exit_code=1)

    fake_ctx.runner.resolve = _no_ssh  # type: ignore[method-assign]
    monkeypatch.setenv("MACCLUSTER_BUSY", "1")
    code = bench.run(
        fake_ctx,
        SimpleNamespace(target=None, mesh=True, peer="node-b", force=False, duration=1),
    )
    assert code == DEGRADED
    out = capsys.readouterr().out
    assert "busy" in out.lower()
