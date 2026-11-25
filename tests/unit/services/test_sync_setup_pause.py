"""An operator pause of docsync (hold file or `launchctl disable`) survives `maccluster sync setup`
and shows up in `doctor` (incident 2026-11-25: the agent came back after a bootout).

Setup and doctor run against the recording fake cluster; only the pause script itself runs in a
real bash, with a stub `launchctl` first on PATH and HOME in a temp dir."""

from __future__ import annotations

import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest
import sync_setup_fakes as fakes
from sync_setup_fakes import LEGACY_CONFIG, THREE_NODES, FakeCluster
from test_sync_setup import _doctor_out

from maccluster.ports.process import ProcessResult

_REAL_RUN, _REAL_POPEN = subprocess.run, subprocess.Popen


@pytest.fixture(autouse=True)
def _no_real_subprocess(monkeypatch):
    def boom(*a, **k):
        raise AssertionError(f"real subprocess call in a unit test: {a}")

    monkeypatch.setattr(subprocess, "run", boom)
    monkeypatch.setattr(subprocess, "Popen", boom)


@pytest.fixture
def cluster(fake_ctx):
    Path(fake_ctx.config_path).write_text(THREE_NODES, encoding="utf-8")
    fake = FakeCluster()
    fake_ctx.runner = fake
    fake_ctx.clock._now = datetime(2026, 11, 25, 15, 0, 0, tzinfo=UTC)
    return fake


def _setup(ctx, **kw):
    from maccluster.services.sync_setup import setup

    return {n.name: n for n in setup(ctx, **kw).nodes}


def _node_text(cluster: FakeCluster, name: str) -> str:
    return "\n".join(" ".join(c) for c in cluster.calls if cluster._target(c) == name)


def _assert_nothing_started(cluster: FakeCluster, name: str) -> None:
    assert "install-agent" not in cluster.node_ops(name)
    text = _node_text(cluster, name)
    assert "launchctl bootstrap" not in text
    assert "launchctl enable" not in text
    assert "launchctl kickstart" not in text


# (a) ─────────────────────────────────────────────────────────────────────────────────────────
def test_hold_file_blocks_agent_install(fake_ctx, cluster):
    cluster.nodes["node-a"].hold = "incident 2026-11-25\n"
    cluster.nodes["node-b"].hold = ""
    held_loaded = cluster.nodes["node-c"]
    held_loaded.hold = "incident 2026-11-25"
    held_loaded.agent_args, held_loaded.agent_loaded = list(fakes.OWN_ARGS), True

    dry = _setup(fake_ctx, dry_run=True)
    assert dry["node-a"].agent_state == "held"

    by = _setup(fake_ctx)
    assert by["node-a"].agent_state == "held"
    assert "agent: held — incident 2026-11-25" in by["node-a"].message
    assert by["node-b"].agent_state == "held"
    assert "agent: held — no reason given" in by["node-b"].message
    # a loaded agent is reported, never booted out by setup
    assert by["node-c"].agent_state == "held (agent still loaded)"
    assert held_loaded.agent_loaded is True
    assert "agent-bootout" not in cluster.node_ops("node-c")
    for name in ("node-a", "node-b", "node-c"):
        _assert_nothing_started(cluster, name)
        # docsync does not run, so the old sync-home job is not retired either
        assert "sync-home-remove" not in cluster.node_ops(name)
    assert cluster.nodes["node-a"].agent_loaded is False


def test_held_setup_does_not_fail(fake_ctx, cluster):
    from maccluster.services.sync_setup import setup

    for node in cluster.nodes.values():
        node.hold = "incident 2026-11-25"
    assert setup(fake_ctx).exit_code == 0


# (b) ─────────────────────────────────────────────────────────────────────────────────────────
def test_disabled_label_is_not_reinstalled(fake_ctx, cluster):
    cluster.nodes["node-a"].launchd_disabled = True
    unreadable = cluster.nodes["node-b"]
    unreadable.launchd_disabled = True
    unreadable.print_disabled_fails = True

    by = _setup(fake_ctx)
    assert by["node-a"].agent_state == "disabled"
    assert "agent: disabled — launchctl disable com.a321.docsync" in by["node-a"].message
    _assert_nothing_started(cluster, "node-a")
    # an unreadable disabled list is no pause: normal install
    assert by["node-b"].agent_state == "loaded"
    assert cluster.node_ops("node-b").count("install-agent") == 1
    assert by["node-c"].agent_state == "loaded"


def test_pause_script_reads_hold_and_disabled_list(tmp_path, monkeypatch):
    """The real shell snippet: hold file first, then `launchctl print-disabled`."""
    monkeypatch.setattr(subprocess, "Popen", _REAL_POPEN)  # bash only, stub launchctl
    from maccluster.services.sync_setup_agent import AGENT_PAUSE, parse_pause

    home, stub = tmp_path / "home", tmp_path / "bin"
    (home / ".cache").mkdir(parents=True)
    stub.mkdir()
    launchctl = stub / "launchctl"
    launchctl.write_text(
        '#!/bin/sh\n[ "$1" = print-disabled ] || exit 99\n'
        '[ -f "$HOME/rc" ] && exit "$(cat "$HOME/rc")"\n'
        "printf 'disabled services = {\\n\\t\"com.other\" => enabled\\n'\n"
        'cat "$HOME/disabled" 2>/dev/null\nprintf "}\\n"\n'
    )
    launchctl.chmod(0o755)
    env = {"HOME": str(home), "PATH": f"{stub}:/usr/bin:/bin"}

    def run() -> tuple[str, str] | None:
        res = _REAL_RUN(["/bin/bash", "-c", AGENT_PAUSE], env=env, capture_output=True, text=True)
        assert res.returncode == 0, res.stderr
        return parse_pause(res.stdout)

    assert run() is None
    (home / "disabled").write_text('\t"com.a321.docsync-heal" => disabled\n')
    assert run() is None  # the heal label alone is not the sync agent
    (home / "disabled").write_text('\t"com.a321.docsync" => disabled\n')
    assert run() == ("disabled", "launchctl disable com.a321.docsync")
    (home / "disabled").write_text('\t"com.a321.docsync" => true\n')  # older macOS wording
    assert run() == ("disabled", "launchctl disable com.a321.docsync")
    (home / "rc").write_text("1")
    assert run() is None  # print-disabled failed: not disabled
    (home / ".cache" / "docsync.hold").write_text("incident 2026-11-25\nsecond line\n")
    assert run() == ("hold", "incident 2026-11-25 second line")
    (home / ".cache" / "docsync.hold").write_text("")
    assert run() == ("hold", "no reason given")
    assert os.environ.get("HOME") != str(home)


# (d) ─────────────────────────────────────────────────────────────────────────────────────────
def test_doctor_reports_paused_docsync(fake_ctx, cluster, monkeypatch):
    from maccluster.domain.enums import ReachabilityState
    from maccluster.services.config_service import load_and_bind_self
    from maccluster.services.sync_setup_doctor import sync_findings

    cfg_dir = Path.home() / ".config" / "docsync"
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "config.toml").write_text(LEGACY_CONFIG)
    now = fake_ctx.clock.now()
    missing = "agent=loaded\n", "agent=missing\n"
    outputs = {
        "node-a": "hold=1\nholdreason=incident 2026-11-25\n"
        + _doctor_out("node-a", success_age_h=5, now=now).replace(*missing),
        "node-b": "disabled=1\n"
        + _doctor_out("node-b", success_age_h=5, now=now).replace(*missing),
        "node-c": _doctor_out("node-c", success_age_h=5, now=now).replace(*missing),
    }
    scripts = []

    def run(argv, *, timeout=15.0, check=False):
        full = tuple(argv)
        scripts.append(" ".join(full))
        return ProcessResult(full, 0, outputs[cluster._target(full)], "")

    monkeypatch.setattr(cluster, "run", run)
    cfg, self_node = load_and_bind_self(fake_ctx)
    states = {"node-b": ReachabilityState.UP, "node-c": ReachabilityState.UP}
    findings = sync_findings(fake_ctx, cfg, self_node, peer_states=states)
    by = {f.summary.split()[0]: f.summary for f in findings}

    assert sum("docsync paused (hold)" in s for s in by.values()) == 1
    assert "docsync paused (hold): incident 2026-11-25" in by["node-a"]
    assert "docsync paused (disabled): launchctl disable com.a321.docsync" in by["node-b"]
    for name in ("node-a", "node-b"):
        assert "not loaded" not in by[name]
    assert "agent com.a321.docsync not loaded" in by["node-c"]
    assert all("docsync.hold" in s and "print-disabled" in s for s in scripts)
