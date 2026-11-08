"""`sync setup` review round 1: foreign agents, sync-home order, peer renames, comments, doctor
success, and the generated bash run for real (GOAL 4)."""

from __future__ import annotations

import subprocess
import tomllib
from datetime import UTC, datetime
from pathlib import Path

import pytest
import sync_setup_fakes as fakes
from sync_setup_fakes import FOREIGN_ARGS, FOREIGN_PY, LEGACY_CONFIG, THREE_NODES, FakeCluster

REAL_RUN = subprocess.run
REAL_POPEN = subprocess.Popen


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
    fake_ctx.clock._now = datetime(2026, 11, 8, 1, 0, 0, tzinfo=UTC)
    return fake


def _setup(ctx, **kw):
    from maccluster.services.sync_setup import setup

    return setup(ctx, **kw)


def _msg(result, name):
    return next(n.message for n in result.nodes if n.name == name)


def test_foreign_agent_is_booted_out_before_llm_roots(fake_ctx, cluster):
    b = cluster.nodes["node-b"]
    b.agent_args, b.agent_loaded = list(FOREIGN_ARGS), True
    result = _setup(fake_ctx)
    assert result.exit_code == 0, _msg(result, "node-b")
    ops = cluster.node_ops("node-b")
    assert ops.count("agent-bootout") == 1 and ops.count("install-agent") == 1
    assert ops.index("docsync") < ops.index("agent-bootout") < ops.index("write-config")
    assert ops.index("write-config") < ops.index("add-llm") < ops.index("install-agent")
    assert b.agent_args == fakes.OWN_ARGS
    assert f"foreign agent {FOREIGN_PY} (docsync 1.2.0) booted out" in _msg(result, "node-b")
    assert "agent-bootout" not in cluster.node_ops("node-a")


def test_foreign_agent_that_stays_loaded_gets_no_llm_roots(fake_ctx, cluster):
    b = cluster.nodes["node-b"]
    b.agent_args, b.agent_loaded, b.bootout_fails = list(FOREIGN_ARGS), True, True
    result = _setup(fake_ctx)
    assert result.exit_code == 1
    assert b.config == LEGACY_CONFIG and "llm-" not in b.config and b.writes == 0
    assert not {"write-config", "add-llm", "install-agent"} & set(cluster.node_ops("node-b"))
    assert "could not be booted out — llm-* roots not written" in _msg(result, "node-b")


def test_paused_foreign_plist_is_replaced_without_bootout(fake_ctx, cluster):
    b = cluster.nodes["node-b"]
    b.agent_args, b.agent_loaded = list(FOREIGN_ARGS), False
    result = _setup(fake_ctx)
    assert result.exit_code == 0
    assert "agent-bootout" not in cluster.node_ops("node-b")
    assert b.agent_args == fakes.OWN_ARGS and b.agent_loaded


def test_dry_run_names_the_foreign_agent(fake_ctx, cluster):
    b = cluster.nodes["node-b"]
    b.agent_args, b.agent_loaded = list(FOREIGN_ARGS), True
    result = _setup(fake_ctx, dry_run=True)
    assert f"would replace foreign agent {FOREIGN_PY} (docsync 1.2.0)" in _msg(result, "node-b")
    assert "agent-bootout" not in cluster.node_ops("node-b") and b.writes == 0


def test_stale_docsync_elsewhere_is_a_warning(fake_ctx, cluster):
    cluster.nodes["node-a"].others = [("/opt/homebrew/bin/docsync", "1.2.0"), ("/x/new", "1.3.1")]
    result = _setup(fake_ctx)
    msg = _msg(result, "node-a")
    assert "stale docsync 1.2.0 at /opt/homebrew/bin/docsync on node-a — upgrade or remove" in msg
    assert "/x/new" not in msg
    assert result.exit_code == 0


def test_sync_home_kept_when_agent_install_fails(fake_ctx, cluster):
    cluster.nodes["node-c"].install_fails = True
    result = _setup(fake_ctx)
    assert result.exit_code == 1
    assert "sync-home-remove" not in cluster.node_ops("node-c")
    assert cluster.nodes["node-c"].sync_home is True
    assert "sync-home kept (agent not loaded)" in _msg(result, "node-c")
    assert cluster.node_ops("node-a").count("sync-home-remove") == 1


def test_peer_rename_skips_the_node(fake_ctx, cluster):
    a = cluster.nodes["node-a"]
    a.config = LEGACY_CONFIG + '\n[[peers]]\nname = "mini-b"\nhosts = ["10.42.0.2"]\n'
    result = _setup(fake_ctx)
    assert "skipped: node-a peer mini-b would be renamed to node-b" in _msg(result, "node-a")
    assert a.writes == 0 and result.exit_code == 1


def test_peer_rename_matches_local_names_too():
    from maccluster.services.sync_setup_config import PeerEntry, PeerRename, plan_config

    text = 'path = "~/Documents"\n\n[[peers]]\nname = "studio"\nhosts = ["MAC-MINI-B.local"]\n'
    with pytest.raises(PeerRename, match="peer studio would be renamed to node-b"):
        plan_config(text, [PeerEntry("node-b", ("10.42.0.2", "mac-mini-b.local"), "u")])
    unrelated = 'path = "~/Documents"\n\n[[peers]]\nname = "nas"\nhosts = ["nas.local"]\n'
    out = plan_config(unrelated, [PeerEntry("node-b", ("10.42.0.2", "mac-mini-b.local"), "u")])
    assert [p["name"] for p in tomllib.loads(out)["peers"]] == ["node-b"]


def test_comment_before_next_table_is_kept():
    from maccluster.services.sync_setup_config import PeerEntry, plan_config

    text = (
        'path = "~/Documents"\n\n[[peers]]\nname = "node-b"\nhosts = ["old"]\n'
        '# peer comment inside the block\nuser = "u"\n\n'
        '# my roots — keep me\n[[roots]]\nname = "desktop"\npath = "~/Desktop"\n'
    )
    out = plan_config(text, [PeerEntry("node-b", ("10.42.0.2", "mac-mini-b.local"), "u")])
    assert "# my roots — keep me\n[[roots]]" in out
    assert "peer comment inside the block" not in out
    assert tomllib.loads(out)["peers"][0]["hosts"] == ["10.42.0.2", "mac-mini-b.local"]


def test_doctor_ignores_all_busy_and_dry_run_reports():
    from maccluster.services.sync_setup_doctor import last_success

    history = (
        "\n=== 2026-11-08 00:10:00\n══ TOTAL\n   peers        2 ok, 0 failed\n"
        "\n=== 2026-11-08 00:20:00\n══ TOTAL\n   peers        0 ok, 0 failed, 2 busy (node-b, node-c)\n"
        "\n=== 2026-11-08 00:30:00\n══ TOTAL\n   peers        1 ok, 0 failed, 1 busy (node-c)\n"
        "\n=== 2026-11-08 00:40:00\n══ TOTAL (DRY RUN)\n   peers        2 ok, 0 failed\n"
    )
    assert last_success(history).strftime("%H:%M") == "00:10"
    assert (
        last_success("\n=== 2026-11-08 00:20:00\n══ TOTAL\n   peers        0 ok, 0 failed\n")
        is None
    )


def test_generated_bash_runs_for_real(tmp_path, monkeypatch):
    """Goodhart guard: run the real write-config and dotfiles-status scripts with /bin/bash."""
    from maccluster.services.sync_setup import DOTFILES_STATUS, _write_script
    from maccluster.services.sync_setup_remote import script

    def bash_only(argv, *a, **k):
        assert list(argv)[:2] == ["/bin/bash", "-c"], argv  # nothing but /bin/bash, here only
        return REAL_POPEN(argv, *a, **k)

    monkeypatch.setattr(subprocess, "Popen", bash_only)
    monkeypatch.setattr(subprocess, "run", REAL_RUN)
    home = tmp_path / "home"
    home.mkdir()
    env = {"HOME": str(home), "PATH": "/usr/bin:/bin", "LANG": "C"}

    def bash(text: str) -> str:
        res = subprocess.run(["/bin/bash", "-c", text], env=env, capture_output=True, text=True)
        assert res.returncode == 0, res.stderr
        return res.stdout.strip()

    cfg = home / ".config" / "docsync" / "config.toml"
    first = 'path = "~/Documents"\nnote = "ä \' quote"\n'
    bash(script("write-config", _write_script(first, "20261108T010000Z")))
    assert cfg.read_text() == first
    assert not list(cfg.parent.glob("*.bak-*"))
    bash(script("write-config", _write_script("x = 1\n", "20261108T020000Z")))
    assert cfg.read_text() == "x = 1\n"
    assert (cfg.parent / "config.toml.bak-20261108T020000Z").read_text() == first

    status = script("dotfiles-status", DOTFILES_STATUS)
    assert bash(status) == "missing"
    (home / "dotfiles").mkdir()
    assert bash(status) == "notgit"
    bash(
        'cd "$HOME/dotfiles" && git init -q && echo a > f && git add f && '
        "git -c user.email=t@t -c user.name=t commit -qm init"
    )
    assert bash(status) == "clean"
    (home / "dotfiles" / "f").write_text("changed\n")
    assert bash(status) == "dirty"


def test_lock_probe_reads_the_holder_without_taking_the_lock(tmp_path, monkeypatch):
    """Real bash + python: free without a record or with a dead pid, busy with a live pid,
    and the probe never calls flock."""
    import os
    import sys

    from maccluster.services.sync_setup import LOCK_PROBE
    from maccluster.services.sync_setup_remote import script

    assert "flock" not in LOCK_PROBE

    def bash_only(argv, *a, **k):
        assert list(argv)[:2] == ["/bin/bash", "-c"], argv
        return REAL_POPEN(argv, *a, **k)

    monkeypatch.setattr(subprocess, "Popen", bash_only)
    monkeypatch.setattr(subprocess, "run", REAL_RUN)
    state = tmp_path / "state"
    state.mkdir()
    env = {"HOME": str(tmp_path), "PATH": "/usr/bin:/bin", "DOCSYNC_STATE_DIR": str(state)}
    probe = f"py={sys.executable}\n" + script("lock", LOCK_PROBE)

    def run() -> str:
        res = subprocess.run(["/bin/bash", "-c", probe], env=env, capture_output=True, text=True)
        assert res.returncode == 0, res.stderr
        return res.stdout.strip()

    assert run() == "free"
    lock = state / "run.lock"
    lock.write_text("99999999\n1762560000\ndocsync sync --all\n")
    assert run() == "free"
    lock.write_text(f"{os.getpid()}\n1762560000\ndocsync sync --all\n")
    out = run()
    assert out.startswith("busy:") and f"pid {os.getpid()}" in out
    assert lock.read_text().startswith(str(os.getpid()))
