"""mesh bench: TB-bound pairs, busy skip, no LAN IPs."""

from __future__ import annotations

import json

import pytest

from maccluster.adapters.iperf3 import FakeBench
from maccluster.cli.exit_codes import DEGRADED, ERROR, USAGE
from maccluster.domain.enums import BenchQuality
from maccluster.errors import CliError
from maccluster.ports.process import ProcessResult
from maccluster.services.mesh_bench_service import exit_for_mesh_report, run_mesh_bench


class NoSshRunner:
    def resolve(self, basename: str) -> str:
        raise CliError(f"tool not found: {basename}", exit_code=1)

    def run(self, argv, *, timeout: float = 15.0, check: bool = False) -> ProcessResult:
        raise AssertionError(f"unexpected run {argv}")


class RecordingSshRunner:
    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []

    def resolve(self, basename: str) -> str:
        if basename in {"ssh", "iperf3"}:
            return f"/usr/bin/{basename}"
        raise CliError(f"tool not found: {basename}", exit_code=1)

    def run(self, argv, *, timeout: float = 15.0, check: bool = False) -> ProcessResult:
        full = tuple(str(a) for a in argv)
        self.calls.append(full)
        remote = full[-1] if full else ""
        if remote == "true":
            return ProcessResult(argv=full, returncode=0, stdout="", stderr="")
        if "iperf3 -s" in remote:
            return ProcessResult(argv=full, returncode=0, stdout="", stderr="")
        if "iperf3 -c" in remote:
            payload = {"end": {"sum_sent": {"bits_per_second": 37_120_000_000.0, "retransmits": 0}}}
            return ProcessResult(
                argv=full,
                returncode=0,
                stdout=json.dumps(payload),
                stderr="",
            )
        return ProcessResult(argv=full, returncode=1, stdout="", stderr="unexpected")


def _ctx(fake_ctx, *, runner=None, bench=None):
    if runner is not None:
        fake_ctx.runner = runner
    if bench is not None:
        fake_ctx.bench = bench
    return fake_ctx


def _idle(tmp_path, env=None):
    return {"env": env if env is not None else {}, "busy_path": tmp_path / "busy"}


def test_mesh_requires_peer(fake_ctx, tmp_path):
    with pytest.raises(CliError) as ei:
        run_mesh_bench(
            _ctx(fake_ctx, runner=NoSshRunner()),
            peer="no-such",
            **_idle(tmp_path),
        )
    assert ei.value.exit_code == USAGE


def test_busy_skips_without_calling_iperf(fake_ctx, tmp_path):
    bench = FakeBench()
    calls = []
    orig = bench.run

    def wrapped(target, *, duration=5, bind_ip=None):
        calls.append(target)
        return orig(target, duration=duration, bind_ip=bind_ip)

    bench.run = wrapped  # type: ignore[method-assign]
    report = run_mesh_bench(
        _ctx(fake_ctx, runner=NoSshRunner(), bench=bench),
        **_idle(tmp_path, {"MACCLUSTER_BUSY": "1"}),
    )
    assert report.busy_skipped is True
    assert report.paths == ()
    assert calls == []
    assert exit_for_mesh_report(report) == DEGRADED


def test_force_runs_when_busy(fake_ctx, tmp_path):
    report = run_mesh_bench(
        _ctx(fake_ctx, runner=NoSshRunner(), bench=FakeBench(mbps=35_000.0)),
        force=True,
        peer="node-b",
        **_idle(tmp_path, {"MACCLUSTER_BUSY": "1"}),
    )
    assert report.busy_skipped is False
    assert report.orchestrated is False
    assert len(report.paths) == 1
    assert report.paths[0].src_id == "node-a"
    assert report.paths[0].dst_id == "node-b"
    assert report.paths[0].ok
    assert report.paths[0].quality == BenchQuality.EXCELLENT


def test_orchestrated_pairs_bind_server_to_dst(fake_ctx, tmp_path):
    runner = RecordingSshRunner()
    report = run_mesh_bench(
        _ctx(fake_ctx, runner=runner, bench=FakeBench(mbps=35_000.0)),
        peer="node-b",
        duration=2,
        **_idle(tmp_path),
    )
    assert report.orchestrated is True
    ids = [(p.src_id, p.dst_id) for p in report.paths]
    assert ("node-a", "node-b") in ids
    assert ("node-b", "node-a") in ids
    remotes = [c[-1] for c in runner.calls]
    assert any("iperf3 -s" in r and "-B 10.42.0.2" in r for r in remotes)
    assert any("iperf3 -c 10.42.0.1" in r and "-B 10.42.0.2" in r for r in remotes)


def test_missing_local_iperf_is_error(fake_ctx, tmp_path):
    with pytest.raises(CliError) as ei:
        run_mesh_bench(
            _ctx(fake_ctx, runner=NoSshRunner(), bench=FakeBench(available=False)),
            peer="node-b",
            **_idle(tmp_path),
        )
    assert ei.value.exit_code == ERROR


def test_poor_path_is_degraded(fake_ctx, tmp_path):
    report = run_mesh_bench(
        _ctx(fake_ctx, runner=NoSshRunner(), bench=FakeBench(mbps=40.0)),
        peer="node-b",
        **_idle(tmp_path),
    )
    assert report.paths[0].quality == BenchQuality.POOR
    assert exit_for_mesh_report(report) == DEGRADED
