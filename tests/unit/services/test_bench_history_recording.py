"""mesh bench + speedtest append to the bench history (no network)."""

from __future__ import annotations

import json

from maccluster.adapters.iperf3 import FakeBench
from maccluster.errors import CliError
from maccluster.ports.process import ProcessResult
from maccluster.services.bench_history import read_samples
from maccluster.services.mesh_bench_service import run_mesh_bench
from maccluster.services.speedtest_service import run_speedtest


class NoSshRunner:
    def resolve(self, basename: str) -> str:
        raise CliError(f"tool not found: {basename}", exit_code=1)

    def run(self, argv, *, timeout: float = 15.0, check: bool = False) -> ProcessResult:
        raise AssertionError(f"unexpected run {argv}")


def test_mesh_bench_records_samples(fake_ctx, tmp_path):
    fake_ctx.runner = NoSshRunner()
    fake_ctx.bench = FakeBench(mbps=35_000.0)
    hist = tmp_path / "hist.jsonl"
    report = run_mesh_bench(
        fake_ctx,
        peer="node-b",
        env={},
        busy_path=tmp_path / "busy",
        history_path=hist,
    )
    assert len(report.paths) == 1
    got = read_samples(path=hist)
    assert [(s.peer, s.transport, s.mbps, s.source) for s in got] == [
        ("node-b", "tb", 35_000.0, "mesh")
    ]
    assert got[0].duration_s == 5


def test_mesh_bench_busy_skip_records_nothing(fake_ctx, tmp_path):
    fake_ctx.runner = NoSshRunner()
    hist = tmp_path / "hist.jsonl"
    run_mesh_bench(
        fake_ctx,
        env={"MACCLUSTER_BUSY": "1"},
        busy_path=tmp_path / "busy",
        history_path=hist,
    )
    assert not hist.exists()


def test_mesh_bench_history_write_failure_does_not_break_bench(fake_ctx, tmp_path):
    fake_ctx.runner = NoSshRunner()
    fake_ctx.bench = FakeBench(mbps=35_000.0)
    blocker = tmp_path / "file"
    blocker.write_text("x", encoding="utf-8")
    report = run_mesh_bench(
        fake_ctx,
        peer="node-b",
        env={},
        busy_path=tmp_path / "busy",
        history_path=blocker / "hist.jsonl",  # parent is a file → OSError on mkdir
    )
    assert report.paths[0].ok


def test_speedtest_records_samples(fake_ctx, tmp_path):
    fake_ctx.runner = NoSshRunner()
    fake_ctx.bench = FakeBench(mbps=30_000.0)
    hist = tmp_path / "hist.jsonl"
    report = run_speedtest(
        fake_ctx,
        peer="node-b",
        try_start_server=False,
        busy_path=tmp_path / "busy",
        history_path=hist,
    )
    assert report.peers[0].iperf_ok
    rows = [json.loads(line) for line in hist.read_text(encoding="utf-8").splitlines()]
    assert [(r["peer"], r["transport"], r["mbps"], r["source"]) for r in rows] == [
        ("node-b", "tb", 30_000.0, "speedtest")
    ]


def test_speedtest_cable_only_records_nothing(fake_ctx, tmp_path):
    fake_ctx.runner = NoSshRunner()
    hist = tmp_path / "hist.jsonl"
    run_speedtest(fake_ctx, peer="node-b", skip_iperf=True, history_path=hist)
    assert not hist.exists()


def test_default_history_path_is_env_scoped_in_tests(fake_ctx, tmp_path, monkeypatch):
    """The autouse conftest fixture keeps every test off the real ~/.local/state."""
    import os

    from maccluster.services.bench_history import BENCH_HISTORY_ENV, default_bench_history_path

    env_path = os.environ.get(BENCH_HISTORY_ENV)
    assert env_path, "conftest must set MACCLUSTER_BENCH_HISTORY"
    assert str(default_bench_history_path()) == env_path
    assert str(tmp_path) in env_path
