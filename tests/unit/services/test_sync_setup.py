"""`maccluster sync setup`: one docsync config, agent and dotfiles clone per node (GOAL 4).

Every test drives a recording fake runner that simulates three Macs; a guard fails the test on
any real subprocess call (no real ssh, launchctl or git)."""

from __future__ import annotations

import re
import subprocess
import tomllib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
import sync_setup_fakes as fakes
from sync_setup_fakes import FOREIGN_ARGS, FOREIGN_PY, LEGACY_CONFIG, THREE_NODES, FakeCluster

from maccluster.errors import CliError
from maccluster.ports.process import ProcessResult


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


def _by_name(result):
    return {n.name: n for n in result.nodes}


# (a) ─────────────────────────────────────────────────────────────────────────────────────────
def test_config_per_node(fake_ctx, cluster):
    dry = _setup(fake_ctx, dry_run=True)
    assert all(x.writes == 0 for x in cluster.nodes.values())
    assert not any(op in {"write-config", "add-llm", "install-agent"} for _, op in cluster.ops)
    assert all("would" in n.message for n in dry.nodes)

    result = _setup(fake_ctx)
    assert result.exit_code == 0, [n.message for n in result.nodes]
    ips = {"node-a": "10.42.0.1", "node-b": "10.42.0.2", "node-c": "10.42.0.3"}
    for name, node in cluster.nodes.items():
        cfg = tomllib.loads(node.config)
        assert cfg["path"] == "~/Documents"
        assert [r["name"] for r in cfg["roots"]] == [
            "desktop",
            "llm-claude",
            "llm-codex",
            "llm-gemini",
        ]
        assert cfg["roots"][0]["path"] == "~/Desktop"
        others = [n for n in ips if n != name]
        assert [p["name"] for p in cfg["peers"]] == others
        for p in cfg["peers"]:
            assert p["hosts"] == [ips[p["name"]], f"mac-mini-{p['name'][-1]}.local"]
        assert cfg["skip_on_battery"] is False
        assert cfg["excludes"] == [".DS_Store", "*.custom"]
        assert cfg["conflict_policy"] == "newest"
        assert list(node.backups) == ["20261108T010000Z"]
        assert node.backups["20261108T010000Z"] == LEGACY_CONFIG
        assert (
            "config written (backup config.toml.bak-20261108T010000Z"
            in _by_name(result)[name].message
        )
        assert _by_name(result)[name].config_changed is True


# (b) ─────────────────────────────────────────────────────────────────────────────────────────
def test_one_agent_per_node(fake_ctx, cluster):
    result = _setup(fake_ctx)
    assert result.exit_code == 0
    for name, node in cluster.nodes.items():
        ops = cluster.node_ops(name)
        assert ops.count("sync-home-remove") == 1
        assert ops.count("install-agent") == 1
        assert node.sync_home is False
        assert node.agent_args == fakes.OWN_ARGS and node.agent_loaded
        assert _by_name(result)[name].agent_state == "loaded"
    removes = [" ".join(c) for c in cluster.calls if "# op:sync-home-remove" in " ".join(c)]
    assert len(removes) == 3
    for cmd in removes:
        assert "launchctl bootout" in cmd and "com.maccluster.sync-home" in cmd
        assert "com.maccluster.sync-home.plist" in cmd
    installs = [" ".join(c) for c in cluster.calls if "# op:install-agent" in " ".join(c)]
    assert len(installs) == 3
    assert all("-m docsync install-agent --interval 15" in c for c in installs)


def test_agent_without_max_runtime_is_a_failure(fake_ctx, cluster, monkeypatch):
    monkeypatch.setattr(
        fakes, "OWN_ARGS", [a.replace("--max-runtime", "--other") for a in fakes.OWN_ARGS]
    )
    result = _setup(fake_ctx)
    assert result.exit_code == 1
    assert all("max-runtime" in n.agent_state for n in result.nodes)


# (c) ─────────────────────────────────────────────────────────────────────────────────────────
def test_dirty_dotfiles_untouched(fake_ctx, cluster):
    cluster.nodes["node-a"].dotfiles = "dirty"
    cluster.nodes["node-b"].dotfiles = "missing"
    cluster.nodes["node-c"].dotfiles = "behind"
    result = _setup(fake_ctx)
    nodes = _by_name(result)
    a_ops = cluster.node_ops("node-a")
    assert not {"dotfiles-clone", "dotfiles-ff", "dotfiles-fetch"} & set(a_ops)
    assert nodes["node-a"].dotfiles_state == "dirty"
    assert "dotfiles dirty — left unchanged" in nodes["node-a"].message
    assert cluster.node_ops("node-b").count("dotfiles-clone") == 1
    clone = [" ".join(c) for c in cluster.calls if "# op:dotfiles-clone" in " ".join(c)]
    assert len(clone) == 1
    assert "git clone git@github.com:maf4711/dotfiles.git" in clone[0]
    assert nodes["node-b"].dotfiles_state == "cloned"
    assert cluster.node_ops("node-c").count("dotfiles-ff") == 1
    ff = [" ".join(c) for c in cluster.calls if "# op:dotfiles-ff" in " ".join(c)]
    assert "merge --ff-only" in ff[0]
    assert "dotfiles fast-forwarded 18 commits" in nodes["node-c"].message
    for word in ("reset", "stash", "checkout"):
        assert not any(word in " ".join(c) for c in cluster.calls if "dotfiles" in " ".join(c))
    assert result.exit_code == 0


def test_diverged_dotfiles_untouched(fake_ctx, cluster):
    cluster.nodes["node-b"].dotfiles = "diverged"
    result = _setup(fake_ctx)
    assert "dotfiles-ff" not in cluster.node_ops("node-b")
    assert _by_name(result)["node-b"].dotfiles_state == "diverged"
    assert "node-b" in render(result)


def render(result) -> str:
    from maccluster.services.sync_setup import render_setup

    return render_setup(result)


# (d) ─────────────────────────────────────────────────────────────────────────────────────────
def test_unreachable_node_and_idempotent_rerun(fake_ctx, cluster):
    cluster.nodes["node-b"].reachable = False
    first = _setup(fake_ctx)
    assert first.exit_code == 1
    assert "skipped: node-b unreachable" in render(first)
    assert cluster.nodes["node-b"].writes == 0
    assert (
        cluster.nodes["node-c"].config is not None and "10.42.0.2" in cluster.nodes["node-c"].config
    )
    # the probe tried the TB address, then the .local name, each within 5 s
    probes = [c for c in cluster.calls if "# op:probe" in " ".join(c)]
    b_probes = [c for c in probes if cluster._target(c) == "node-b"]
    targets = [a for c in b_probes for a in c if "@" in a]
    assert any(t.endswith("@10.42.0.2") for t in targets)
    assert any(t.endswith("@mac-mini-b.local") for t in targets)
    assert all("ConnectTimeout=5" in c for c in b_probes)

    cluster.nodes["node-b"].reachable = True
    _setup(fake_ctx)
    before = cluster.snapshot()
    writes = {n: x.writes for n, x in cluster.nodes.items()}
    again = _setup(fake_ctx)
    assert again.exit_code == 0, render(again)
    assert cluster.snapshot() == before
    assert {n: x.writes for n, x in cluster.nodes.items()} == writes
    assert all(not n.config_changed for n in again.nodes)


def test_old_docsync_gets_no_llm_roots(fake_ctx, cluster):
    cluster.nodes["node-c"].docsync = "1.2.0"
    cluster.nodes["node-b"].probes_ok = False
    result = _setup(fake_ctx)
    assert result.exit_code == 1
    text = render(result)
    assert "docsync 1.2.0 on node-c: run maccluster remote-install 10.42.0.3 first" in text
    assert "docsync 1.3.0 on node-b: run maccluster remote-install 10.42.0.2 first" in text
    for name in ("node-b", "node-c"):
        assert cluster.nodes[name].config == LEGACY_CONFIG
        assert cluster.nodes[name].writes == 0
        assert "llm-" not in cluster.nodes[name].config


def test_busy_lock_waits_then_skips(fake_ctx, cluster):
    cluster.nodes["node-b"].lock_busy = 999
    cluster.nodes["node-c"].lock_busy = 1
    result = _setup(fake_ctx)
    nodes = _by_name(result)
    assert result.exit_code == 1
    assert "another run is in progress (pid 7" in nodes["node-b"].message
    assert cluster.nodes["node-b"].writes == 0
    assert sum(fake_ctx.clock.slept) <= 60 + 10
    assert cluster.nodes["node-c"].writes > 0


def test_node_filter_and_missing_config(fake_ctx, cluster):
    cluster.nodes["node-b"].config = None
    result = _setup(fake_ctx, node="node-b")
    assert [n.name for n in result.nodes] == ["node-b"]
    cfg = tomllib.loads(cluster.nodes["node-b"].config)
    assert cfg["path"] == "~/Documents"
    assert cluster.nodes["node-b"].backups == {}
    assert cluster.nodes["node-a"].writes == 0
    with pytest.raises(CliError):
        _setup(fake_ctx, node="node-x")


def test_python_resolver_covers_both_venv_layouts():
    from maccluster.services.sync_setup_remote import PY_RESOLVE

    assert 'PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:$PATH"' in PY_RESOLVE
    assert "command -v maccluster" in PY_RESOLVE and "realpath" in PY_RESOLVE
    assert "$HOME/.local/pipx/venvs/maccluster/bin/python" in PY_RESOLVE
    assert "$HOME/.local/share/maccluster/venv/bin/python" in PY_RESOLVE


# (e) ─────────────────────────────────────────────────────────────────────────────────────────
def _doctor_out(
    node: str,
    *,
    success_age_h: float,
    now: datetime,
    args: list[str] | None = None,
    pyver: str = "1.3.0",
) -> str:
    ts = (now - timedelta(hours=success_age_h)).astimezone().strftime("%Y-%m-%d %H:%M:%S")
    later = (now - timedelta(minutes=5)).astimezone().strftime("%Y-%m-%d %H:%M:%S")
    checked = int(now.timestamp()) - 600
    icloud = (
        f'{{"checked_at": {checked}, "node": "{node}", '
        '"roots": {"documents": {"ok": true, "dd": true}}}'
    )
    return (
        f"py={fakes.OWN_PY}\npyver=docsync {pyver}\nagent=loaded\nexit=0\n"
        f"== agent\n{fakes.agent_print(args or fakes.OWN_ARGS)}== end\n"
        "dotfiles=clean\nahead_behind=0\t0\n"
        f"== history\n\n=== {ts}\n══ TOTAL\n   peers        2 ok, 0 failed\n"
        f"\n=== {later}\n══ TOTAL\n   peers        1 ok, 1 failed\n== end\n"
        f"== icloud\n{icloud}\n== end\n"
    )


def test_doctor_sync_finding(fake_ctx, cluster, monkeypatch):
    from maccluster.doctor_logic.report import build_report
    from maccluster.domain.enums import CheckSeverity, ReachabilityState
    from maccluster.services.config_service import load_and_bind_self
    from maccluster.services.sync_setup_doctor import sync_findings

    home = Path.home()
    (home / ".config" / "docsync").mkdir(parents=True)
    (home / ".config" / "docsync" / "config.toml").write_text(LEGACY_CONFIG)
    now = fake_ctx.clock.now()
    outputs = {
        "node-a": _doctor_out("node-a", success_age_h=3, now=now),
        "node-c": _doctor_out("node-c", success_age_h=0.2, now=now),
    }

    timeouts = []

    def run(argv, *, timeout=15.0, check=False):
        full = tuple(argv)
        cluster.calls.append(full)
        timeouts.append(timeout)
        assert "# op:doctor" in " ".join(full)
        name = cluster._target(full)
        return ProcessResult(full, 0, outputs[name], "")

    monkeypatch.setattr(cluster, "run", run)
    cfg, self_node = load_and_bind_self(fake_ctx)
    states = {"node-b": ReachabilityState.DOWN, "node-c": ReachabilityState.UP}
    findings = sync_findings(fake_ctx, cfg, self_node, peer_states=states)
    assert [f.check_id for f in findings] == ["sync", "sync", "sync"]
    by = {f.summary.split()[0]: f for f in findings}
    assert by["node-a"].severity == CheckSeverity.WARN
    assert "last docsync success 3 h ago" in by["node-a"].summary
    assert "agent loaded (last exit 0)" in by["node-a"].summary
    assert "icloud ok" in by["node-a"].summary and "dotfiles clean" in by["node-a"].summary
    assert by["node-b"].severity == CheckSeverity.WARN
    assert "unreachable" in by["node-b"].summary
    assert by["node-c"].severity == CheckSeverity.OK
    assert build_report(findings).exit_code == 0
    assert timeouts and max(timeouts) <= 10  # peers are read one after the other
    assert not any("node-b" in a or "10.42.0.2" in a for c in cluster.calls for a in c)


def test_doctor_without_docsync_config_runs_nothing(fake_ctx, cluster):
    from maccluster.domain.enums import CheckSeverity
    from maccluster.services.config_service import load_and_bind_self
    from maccluster.services.sync_setup_doctor import sync_findings

    cfg, self_node = load_and_bind_self(fake_ctx)
    findings = sync_findings(fake_ctx, cfg, self_node, peer_states={})
    assert len(findings) == 1 and findings[0].severity == CheckSeverity.INFO
    assert cluster.calls == []


def test_run_doctor_has_sync_finding(fake_ctx, cluster):
    from maccluster.services.doctor_service import run_doctor

    report = run_doctor(fake_ctx)
    assert any(f.check_id == "sync" for f in report.findings)


# (f) ─────────────────────────────────────────────────────────────────────────────────────────
class _Exit(Exception):
    pass


def _fake_watchdog(with_dump: bool):
    wd = SimpleNamespace(EXIT_DEADLINE=4, armed=None, killed=0)
    wd.arm = lambda seconds, cb: setattr(wd, "armed", cb) or True
    wd.disarm = lambda: None
    wd.kill_descendants = lambda: setattr(wd, "killed", wd.killed + 1)
    if with_dump:

        def dump_stacks(path):
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            Path(path).write_text("Thread MainThread\n")
            return Path(path)

        wd.dump_stacks = dump_stacks
    return wd


@pytest.mark.parametrize("with_dump", [True, False])
def test_deadline_writes_stacks(monkeypatch, capsys, with_dump):
    from maccluster.render.progress import NullProgress
    from maccluster.services import sync_engine

    wd = _fake_watchdog(with_dump)
    monkeypatch.setattr(sync_engine, "_load_watchdog", lambda prog: wd)
    codes: list[int] = []

    def fake_exit(code):
        codes.append(code)
        raise _Exit

    monkeypatch.setattr(sync_engine.os, "_exit", fake_exit)

    def body():
        with pytest.raises(_Exit):
            wd.armed()
        return "done"

    assert sync_engine.run_with_deadline(NullProgress(), 30, body) == "done"
    assert codes == [4] and wd.killed == 1
    files = list((Path.home() / "Library" / "Logs" / "maccluster").glob("deadline-*.txt"))
    assert len(files) == 1
    assert re.fullmatch(r"deadline-\d{8}T\d{6}Z\.txt", files[0].name)
    assert files[0].read_text().strip()
    assert str(files[0]) in capsys.readouterr().err


# docsync request carries the run's timeout as max_runtime ───────────────────────────────────
def test_docsync_request_gets_max_runtime(tmp_path):
    from dataclasses import dataclass, field

    from maccluster.services import sync_docsync

    seen = {}

    @dataclass
    class Req:
        roots: list
        peers: list
        conflict_policy: str = "newest"
        force_deletes: bool = False
        dry_run: bool = False
        log: object = None
        state_dir: object = None
        excludes: list = field(default_factory=list)
        max_runtime: float = 1800

    class Res:
        error = ""
        exit_code = 0
        report = SimpleNamespace(render=lambda: "")

        def to_dict(self):
            return {"peers": []}

    def sync(req):
        seen["req"] = req
        return Res()

    api = SimpleNamespace(
        SyncRequest=Req,
        RootSpec=lambda *a: a,
        PeerSpec=lambda *a, **k: SimpleNamespace(name=a[0], host=a[1], user=a[2], via="LAN"),
        resolve_peer=lambda hosts, user, name, log=None: (hosts[0], "LAN"),
        sync=sync,
        __version__="1.3.0",
    )
    (tmp_path / "Documents").mkdir()
    sync_docsync.run(
        prog=SimpleNamespace(note=lambda *_: None),
        local_home=tmp_path,
        remote_home=str(tmp_path),
        peers=[],
        self_ip="10.42.0.1",
        via="tb",
        target="home",
        includes=("Documents/",),
        excludes=(),
        conflict_policy="newer",
        dry_run=True,
        compare_only=False,
        default_user="u",
        api=api,
        max_runtime=3600,
    )
    assert seen["req"].max_runtime == 3600


def test_cli_dispatch_prints_one_line_per_node(fake_ctx, cluster, capsys):
    from maccluster.cli.parser import build_parser
    from maccluster.commands import sync_cmd

    args = build_parser().parse_args(["sync", "setup", "--dry-run", "--node", "node-b"])
    assert sync_cmd.run(fake_ctx, args) == 0
    out = capsys.readouterr().out.splitlines()
    assert len(out) == 1 and out[0].startswith("node-b ")
    assert all(x.writes == 0 for x in cluster.nodes.values())


def test_doctor_warns_about_a_foreign_or_old_agent(fake_ctx, cluster, monkeypatch):
    from maccluster.doctor_logic.report import build_report
    from maccluster.domain.enums import CheckSeverity, ReachabilityState
    from maccluster.services.config_service import load_and_bind_self
    from maccluster.services.sync_setup_doctor import sync_findings

    cfg_dir = Path.home() / ".config" / "docsync"
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "config.toml").write_text(LEGACY_CONFIG)
    now = fake_ctx.clock.now()
    outputs = {
        "node-a": _doctor_out("node-a", success_age_h=0.1, now=now, args=FOREIGN_ARGS),
        "node-b": _doctor_out("node-b", success_age_h=0.1, now=now, pyver="1.2.0"),
        "node-c": _doctor_out("node-c", success_age_h=0.1, now=now),
    }
    versions = []

    def run(argv, *, timeout=15.0, check=False):
        full = tuple(argv)
        text = " ".join(full)
        name = cluster._target(full)
        if "# op:agent-version" in text:
            versions.append(name)
            assert FOREIGN_PY in text and "--version" in text
            return ProcessResult(full, 0, "docsync 1.2.0\n", "")
        assert "# op:doctor" in text
        return ProcessResult(full, 0, outputs[name], "")

    monkeypatch.setattr(cluster, "run", run)
    cfg, self_node = load_and_bind_self(fake_ctx)
    states = {"node-b": ReachabilityState.UP, "node-c": ReachabilityState.UP}
    by = {
        f.summary.split()[0]: f for f in sync_findings(fake_ctx, cfg, self_node, peer_states=states)
    }
    a = by["node-a"]
    assert a.severity == CheckSeverity.WARN
    assert (
        f"agent runs {FOREIGN_PY}, not maccluster's docsync — run maccluster sync setup"
        in a.summary
    )
    assert "agent docsync 1.2.0 < 1.3.0" in a.summary
    assert versions == ["node-a"]
    assert by["node-b"].severity == CheckSeverity.WARN
    assert "agent docsync 1.2.0 < 1.3.0" in by["node-b"].summary
    assert "agent runs" not in by["node-b"].summary
    assert by["node-c"].severity == CheckSeverity.OK
    assert build_report(list(by.values())).exit_code == 0
