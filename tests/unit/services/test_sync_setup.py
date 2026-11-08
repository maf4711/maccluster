"""`maccluster sync setup`: one docsync config, agent and dotfiles clone per node (GOAL 4).

Every test drives a recording fake runner that simulates three Macs; a guard fails the test on
any real subprocess call (no real ssh, launchctl or git)."""

from __future__ import annotations

import base64
import hashlib
import re
import shlex
import subprocess
import tomllib
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from maccluster.errors import CliError
from maccluster.ports.process import ProcessResult

THREE_NODES = """
schema_version = 1
name = "studio-cluster"
subnet = "10.42.0.0/24"
bridge_interface = "bridge0"
heal_interval_seconds = 30
ssh_probes_enabled = false

[[nodes]]
id = "node-a"
hostnames = ["mac-mini-a.local", "mac-mini-a"]
ip = "10.42.0.1"
hw_uuid = "00000000-0000-0000-0000-000000000001"

[[nodes]]
id = "node-b"
hostnames = ["mac-mini-b.local", "mac-mini-b"]
ip = "10.42.0.2"
hw_uuid = "00000000-0000-0000-0000-000000000002"

[[nodes]]
id = "node-c"
hostnames = ["mac-mini-c.local", "mac-mini-c"]
ip = "10.42.0.3"
hw_uuid = "00000000-0000-0000-0000-000000000003"
"""

IPS = {"10.42.0.1": "node-a", "10.42.0.2": "node-b", "10.42.0.3": "node-c"}
LOCAL = {"mac-mini-a.local": "node-a", "mac-mini-b.local": "node-b", "mac-mini-c.local": "node-c"}
LEGACY_CONFIG = (
    'path = "~/Documents"\n'
    'conflict_policy = "newest"\n'
    "skip_on_battery = false\n"
    'excludes = [".DS_Store", "*.custom"]\n'
)
LLM_BLOCK = "".join(
    f'\n[[roots]]  # added by `docsync roots add-llm`\nname = "{n}"\npath = "~/.{n[4:]}"\n'
    f'includes = ["x"]\n'
    for n in ("llm-claude", "llm-codex", "llm-gemini")
)
AGENT_PRINT = (
    "gui/501/com.a321.docsync = {\n\tstate = not running\n\targuments = {\n"
    "\t\t/py\n\t\t-m\n\t\tdocsync\n\t\tsync\n\t\t--all\n\t\t--max-runtime\n\t\t1800\n\t}\n"
    "\tlast exit code = 0\n}\n"
)


def _sha(text: str | None) -> str:
    return hashlib.sha256((text or "").encode()).hexdigest()


def _script_of(argv: tuple[str, ...]) -> str:
    """The bash script a node runs: `bash -c S` locally, `ssh … '/bin/bash -c S'` remotely."""
    if Path(argv[0]).name == "bash":
        return argv[2]
    return shlex.split(argv[-1])[2]


class FakeNode:
    def __init__(self, name: str) -> None:
        self.name = name
        self.reachable = True
        self.docsync = "1.3.0"
        self.probes_ok = True
        self.config: str | None = LEGACY_CONFIG
        self.backups: dict[str, str] = {}
        self.agent_plist: str | None = None
        self.sync_home = True
        self.dotfiles = "behind"  # missing | behind | clean | dirty | diverged
        self.lock_busy = 0  # number of lock probes that report busy
        self.writes = 0


class FakeCluster:
    """Recording fake runner; `# op:<name>` in each script names the node operation."""

    def __init__(self) -> None:
        self.nodes = {n: FakeNode(n) for n in ("node-a", "node-b", "node-c")}
        self.calls: list[tuple[str, ...]] = []
        self.ops: list[tuple[str, str]] = []

    def resolve(self, basename: str) -> str:
        if basename in ("ssh", "bash", "launchctl", "git"):
            return f"/usr/bin/{basename}"
        raise CliError(f"tool not found: {basename}", exit_code=1)

    def _target(self, argv: tuple[str, ...]) -> str:
        if Path(argv[0]).name == "bash":
            return "node-a"  # the local Mac (fake_ctx binds self = node-a)
        for a in argv:
            host = a.split("@", 1)[1] if "@" in a else ""
            if host in IPS:
                return IPS[host]
            if host in LOCAL:
                return LOCAL[host]
        raise AssertionError(f"no node in {argv}")

    def run(self, argv: Sequence[str], *, timeout: float = 15.0, check: bool = False):
        full = tuple(argv)
        self.calls.append(full)
        text = _script_of(full)
        m = re.search(r"# op:([a-z-]+)", text)
        assert m, f"unexpected command {full}"
        op, name = m.group(1), self._target(full)
        self.ops.append((name, op))
        node = self.nodes[name]
        if not node.reachable and Path(full[0]).name == "ssh":
            return ProcessResult(full, 255, "", "ssh: connect timed out")
        out, rc = self._op(node, op, text)
        return ProcessResult(full, rc, out, "" if rc == 0 else out)

    def _op(self, node: FakeNode, op: str, text: str) -> tuple[str, int]:
        if op == "probe":
            return "", 0
        if op == "docsync":
            ok = "add-llm ok\nicloud-check ok\n" if node.probes_ok else ""
            return f"docsync {node.docsync}\n{ok}", 0
        if op == "lock":
            if node.lock_busy:
                node.lock_busy -= 1
                return "busy: another run is in progress (pid 7, running 3 min, docsync sync)", 0
            return "free", 0
        if op == "read-config":
            return (node.config, 0) if node.config is not None else ("", 1)
        if op == "write-config":
            node.writes += 1
            stamp = re.search(r"\.bak-(\d{8}T\d{6}Z)", text).group(1)
            if node.config is not None:
                node.backups[stamp] = node.config
            node.config = base64.b64decode(
                re.search(r"printf %s '([^']+)'", text).group(1)
            ).decode()
            return "", 0
        if op == "add-llm":
            node.writes += 1
            node.config += LLM_BLOCK
            return "added roots llm-claude, llm-codex, llm-gemini", 0
        if op == "agent-status":
            if node.agent_plist is None:
                return "plist=0\n", 0
            return "plist=1\n" + AGENT_PRINT, 0
        if op == "install-agent":
            node.writes += 1
            node.agent_plist = "<plist>--max-runtime 1800</plist>"
            return "installed", 0
        if op == "sync-home-status":
            return f"loaded={int(node.sync_home)} plist={int(node.sync_home)}", 0
        if op == "sync-home-remove":
            node.writes += 1
            node.sync_home = False
            return "", 0
        if op == "dotfiles-status":
            state = {"missing": "missing", "dirty": "dirty"}.get(node.dotfiles, "clean")
            return state, 0
        if op == "dotfiles-clone":
            node.writes += 1
            node.dotfiles = "clean"
            return "Cloning into '/Users/x/dotfiles'...", 0
        if op == "dotfiles-fetch":
            counts = {"behind": "0\t18", "clean": "0\t0", "diverged": "1\t18"}[node.dotfiles]
            return counts, 0
        if op == "dotfiles-ff":
            node.writes += 1
            node.dotfiles = "clean"
            return "", 0
        raise AssertionError(f"unknown op {op}")

    def node_ops(self, name: str) -> list[str]:
        return [op for n, op in self.ops if n == name]

    def snapshot(self) -> dict[str, tuple[str, str]]:
        return {n: (_sha(x.config), _sha(x.agent_plist)) for n, x in self.nodes.items()}


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
        assert "--max-runtime 1800" in node.agent_plist
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
    import sys

    me = sys.modules[__name__]
    monkeypatch.setattr(me, "AGENT_PRINT", AGENT_PRINT.replace("--max-runtime", "--other"))
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
def _doctor_out(node: str, *, success_age_h: float, now: datetime) -> str:
    ts = (now - timedelta(hours=success_age_h)).astimezone().strftime("%Y-%m-%d %H:%M:%S")
    later = (now - timedelta(minutes=5)).astimezone().strftime("%Y-%m-%d %H:%M:%S")
    checked = int(now.timestamp()) - 600
    icloud = (
        f'{{"checked_at": {checked}, "node": "{node}", '
        '"roots": {"documents": {"ok": true, "dd": true}}}'
    )
    return (
        "agent=loaded\nexit=0\ndotfiles=clean\nahead_behind=0\t0\n"
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

    def run(argv, *, timeout=15.0, check=False):
        full = tuple(argv)
        cluster.calls.append(full)
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
