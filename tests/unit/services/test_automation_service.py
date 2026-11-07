"""Maintenance plans never mutate; real runs isolate failures and preserve ordering."""

from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from maccluster.errors import CliError, DegradedError
from maccluster.services import automation_service as auto
from maccluster.services.package_update import WheelArtifact


@pytest.fixture
def maintenance(two_peer_ctx, tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    events = []

    def ensure(ctx):
        events.append("heal")
        return SimpleNamespace(message="healthy")

    def service(ctx):
        events.append("service")
        return SimpleNamespace(installed=True, running=True, detail="installed")

    def build(ctx, source, **kwargs):
        events.append("build")
        return WheelArtifact(kwargs["destination"] / "maccluster.whl", "0.5.0", "a" * 64)

    def install(ctx, artifact, **kwargs):
        events.append("update")
        return SimpleNamespace(message="installed")

    def doctor(ctx):
        events.append("doctor")
        return SimpleNamespace(findings=(), exit_code=0)

    monkeypatch.setattr(auto, "ensure_local", ensure)
    monkeypatch.setattr(auto, "install_service", service)
    monkeypatch.setattr(auto, "build_wheel", build)
    monkeypatch.setattr(auto, "install_wheel", install)
    monkeypatch.setattr(auto, "run_doctor", doctor)
    return two_peer_ctx, events


def test_dry_run_has_no_mutations_subprocesses_or_receipt(maintenance, tmp_path, monkeypatch):
    ctx, events = maintenance

    def forbidden(*a, **k):
        pytest.fail("dry-run side effect")

    ctx.lock = SimpleNamespace(acquire=forbidden)
    monkeypatch.setattr(auto.tempfile, "TemporaryDirectory", forbidden)
    monkeypatch.setattr(auto, "remote_install", forbidden)
    monkeypatch.setattr(auto, "sync_home", forbidden)
    ctx.runner = SimpleNamespace(run=forbidden)
    report = auto.run_automation(ctx, dry_run=True, sync=True)
    assert report.ok and not events
    assert [s.target for s in report.steps if s.name == "remote-install"] == ["node-b", "node-c"]
    assert not auto.receipt_path().exists()
    assert not (tmp_path / ".config").exists()


def test_failed_peer_stops_remaining_updates_but_runs_doctor(maintenance, monkeypatch):
    ctx, events = maintenance
    artifacts = []

    def remote(ctx, peer, **kwargs):
        events.append(peer)
        artifacts.append(kwargs["wheel_path"])
        assert kwargs["copy_config"] and kwargs["preserve_config"]
        assert not kwargs["preflight_speedtest"] and not kwargs["setup_ssh_config"]
        if peer == "node-b":
            raise CliError("offline")
        return SimpleNamespace(ok=True, message="installed")

    monkeypatch.setattr(auto, "remote_install", remote)
    report = auto.run_automation(ctx)
    assert events == ["heal", "service", "build", "node-b", "doctor"]
    assert len(set(artifacts)) == 1
    assert not report.ok and report.exit_code == 1
    stored = json.loads(auto.receipt_path().read_text())
    assert stored["exit_code"] == 1
    skipped = [s for s in stored["steps"] if s["status"] == "skipped"]
    assert [(s["name"], s["target"]) for s in skipped] == [
        ("remote-install", "node-c"),
        ("update", "local"),
    ]
    assert all("node-b" in s["message"] for s in skipped)
    assert not artifacts[0].parent.parent.exists()


@pytest.mark.parametrize("failure", ["result", "exception", "degraded"])
def test_peer_failure_blocks_sync_and_local_update(maintenance, monkeypatch, failure):
    ctx, events = maintenance

    def remote(ctx, peer, **kwargs):
        events.append(peer)
        if failure == "exception":
            raise RuntimeError("unexpected installer failure")
        if failure == "degraded":
            raise DegradedError("peer health degraded")
        return SimpleNamespace(ok=False, message="peer doctor failed")

    monkeypatch.setattr(auto, "remote_install", remote)
    monkeypatch.setattr(
        auto, "sync_home", lambda *a, **k: pytest.fail("sync after rollout failure")
    )
    report = auto.run_automation(ctx, sync=True)
    assert events == ["heal", "service", "build", "node-b", "doctor"]
    assert report.exit_code == (3 if failure == "degraded" else 1)
    assert [(s.name, s.target) for s in report.steps if s.status == "skipped"] == [
        ("remote-install", "node-c"),
        ("sync", "all peers"),
        ("update", "local"),
    ]


def test_successful_peers_share_one_wheel_and_local_update_is_last(maintenance, monkeypatch):
    ctx, events = maintenance
    wheels = []

    def remote(ctx, peer, **kwargs):
        events.append(peer)
        wheels.append(kwargs["wheel_path"])
        return SimpleNamespace(ok=True, message="installed and healthy")

    monkeypatch.setattr(auto, "remote_install", remote)
    assert auto.run_automation(ctx).ok
    assert events == ["heal", "service", "build", "node-b", "node-c", "doctor", "update"]
    assert len(wheels) == 2 and wheels[0] == wheels[1]


def test_last_peer_failure_also_blocks_local_update(maintenance, monkeypatch):
    ctx, events = maintenance

    def remote(ctx, peer, **kwargs):
        events.append(peer)
        return SimpleNamespace(ok=peer == "node-b", message="health result")

    monkeypatch.setattr(auto, "remote_install", remote)
    report = auto.run_automation(ctx)
    assert not report.ok
    assert events == ["heal", "service", "build", "node-b", "node-c", "doctor"]
    assert report.steps[-1].status == "skipped" and "node-c" in report.steps[-1].message


def test_healing_without_updates_still_attempts_remaining_peers(maintenance, monkeypatch):
    ctx, events = maintenance

    def heal(ctx, *, peer):
        events.append(peer)
        raise CliError("offline")

    monkeypatch.setattr(auto, "run_fleet_heal", heal)
    report = auto.run_automation(ctx, no_update=True)
    assert report.exit_code == 1
    assert events == ["heal", "service", "node-b", "node-c", "doctor"]


def test_no_update_heals_each_peer_and_reports_degraded(maintenance, monkeypatch):
    ctx, events = maintenance

    def fleet(ctx, *, peer):
        events.append(peer)
        return SimpleNamespace(summary="degraded", hops=(), self_degraded=peer == "node-b")

    monkeypatch.setattr(auto, "run_fleet_heal", fleet)
    report = auto.run_automation(ctx, no_update=True)
    assert events == ["heal", "service", "node-b", "node-c", "doctor"]
    assert report.exit_code == 3 and not report.ok


def test_build_failure_continues_local_health_without_rebuilding(maintenance, monkeypatch):
    ctx, events = maintenance

    def fail(*a, **k):
        events.append("build")
        raise CliError("download failed")

    monkeypatch.setattr(auto, "build_wheel", fail)
    report = auto.run_automation(ctx)
    assert events == ["heal", "service", "build", "doctor"]
    assert report.exit_code == 1
    assert len([s for s in report.steps if s.status == "skipped"]) == 3


def test_local_degraded_does_not_hide_doctor_or_update(maintenance, monkeypatch):
    ctx, events = maintenance
    monkeypatch.setattr(
        auto, "ensure_local", lambda ctx: (_ for _ in ()).throw(DegradedError("no cable"))
    )
    report = auto.run_automation(ctx, local_only=True)
    assert report.exit_code == 3
    assert events[-2:] == ["doctor", "update"]


def test_sync_is_opt_in_verified_and_before_update(maintenance, monkeypatch):
    ctx, events = maintenance
    monkeypatch.setattr(
        auto, "remote_install", lambda *a, **k: SimpleNamespace(ok=True, message="installed")
    )

    def sync(ctx, **kwargs):
        events.append("sync")
        assert kwargs["safetynet"] and kwargs["verify"] and kwargs["no_speedtest"]
        assert kwargs["peer"] == "node-c"
        return SimpleNamespace(peers=())

    monkeypatch.setattr(auto, "sync_home", sync)
    monkeypatch.setattr(auto, "exit_code_for_sync", lambda result: 0)
    assert auto.run_automation(ctx, peer="node-c", sync=True).ok
    assert events[-3:] == ["sync", "doctor", "update"]


def test_missing_configuration_is_structured_failure(maintenance):
    ctx, events = maintenance
    ctx.config_path = Path("/missing/cluster.toml")
    report = auto.run_automation(ctx)
    assert report.steps[0].name == "configuration"
    assert report.steps[0].status == "failed" and report.exit_code == 1
    assert not events and auto.receipt_path().exists()


def test_busy_lock_never_overwrites_previous_receipt(maintenance):
    ctx, events = maintenance
    auto.receipt_path().parent.mkdir(parents=True)
    auto.receipt_path().write_text('{"existing": true}')

    @contextmanager
    def busy(path, **kwargs):
        assert path.name == "automation.lock"
        raise CliError("busy")
        yield

    ctx.lock = SimpleNamespace(acquire=busy)
    report = auto.run_automation(ctx)
    assert report.exit_code == 1 and report.steps[0].name == "lock"
    assert auto.read_last_receipt(ctx) == {"existing": True}
    assert not events


def test_update_does_not_require_cluster_config(maintenance):
    ctx, events = maintenance
    ctx.config_path = Path("/missing/cluster.toml")
    assert auto.run_update(ctx).ok
    assert events == ["build", "update"]


def test_receipt_write_failure_is_not_success(maintenance, monkeypatch):
    ctx, events = maintenance

    def fail(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(ctx.fs, "write_text_atomic", fail)
    report = auto.run_update(ctx)
    assert report.exit_code == 1 and report.steps[-1].name == "receipt"
    assert report.receipt_path is None


@pytest.mark.parametrize("options", [{"peer": "node-b"}, {"sync": True}])
def test_local_only_rejects_remote_options(maintenance, options):
    ctx, _ = maintenance
    with pytest.raises(CliError, match="local-only"):
        auto.run_automation(ctx, local_only=True, **options)


def test_loaded_heal_service_waits_for_doctor_without_false_failure(maintenance, monkeypatch):
    ctx, events = maintenance
    monkeypatch.setattr(
        auto,
        "install_service",
        lambda ctx: SimpleNamespace(installed=True, running=False, detail="loaded"),
    )
    report = auto.run_automation(ctx, local_only=True, no_update=True)
    assert report.ok and "doctor" in events
    assert next(step for step in report.steps if step.name == "service").status == "ok"
