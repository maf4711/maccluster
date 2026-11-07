"""Engine switch and docsync adapter (request mapping, result mapping, fallbacks)."""

from __future__ import annotations

from dataclasses import dataclass, field
from ipaddress import IPv4Address
from pathlib import Path

import pytest

from maccluster.domain.models import Node
from maccluster.errors import CliError
from maccluster.services import sync_docsync as sd


def _node(nid: str, ip: str, *names: str) -> Node:
    return Node(id=nid, hostnames=names, ip=IPv4Address(ip), hw_uuid="x")


class _Prog:
    def __init__(self):
        self.notes: list[str] = []

    def note(self, text: str) -> None:
        self.notes.append(text)


def test_resolve_engine_choices(monkeypatch):
    monkeypatch.setattr(sd, "docsync_version", lambda: "1.1.0")
    assert sd.resolve_engine("ditto", []) == ("ditto", "")
    assert sd.resolve_engine("docsync", []) == ("docsync", "")
    monkeypatch.delenv(sd.ENGINE_ENV, raising=False)
    assert sd.resolve_engine(None, []) == ("docsync", "")
    engine, note = sd.resolve_engine("auto", ["--transport rdma"])
    assert engine == "ditto" and "rdma" in note
    with pytest.raises(CliError) as exc:
        sd.resolve_engine("docsync", ["--push-only/--pull-only (docsync is always bidirectional)"])
    assert exc.value.exit_code == 2
    with pytest.raises(CliError):
        sd.resolve_engine("rsyncish", [])


def test_docsync_missing(monkeypatch):
    monkeypatch.setattr(sd, "docsync_version", lambda: None)
    assert sd.resolve_engine("auto", []) == ("ditto", "engine ditto (docsync not installed)")
    with pytest.raises(CliError) as exc:
        sd.resolve_engine("docsync", [])
    assert exc.value.exit_code == 1
    assert "docsync engine not installed" in exc.value.message


def test_ditto_only_reasons():
    base = dict(
        push_only=False,
        pull_only=False,
        transport=None,
        conflict_policy="newer",
        force_icloud=False,
        identical=False,
        quick=False,
        max_files=None,
        max_bytes=None,
    )
    assert sd.ditto_only_reasons(**base) == []
    assert sd.ditto_only_reasons(**{**base, "conflict_policy": "larger"}) == [
        "--conflict-policy larger"
    ]
    assert len(sd.ditto_only_reasons(**{**base, "push_only": True, "transport": "rdma"})) == 2


def test_translate_excludes():
    pats = (
        ".Trash/",
        "Library/Caches/",
        "**/node_modules/",
        "**/*.gguf",
        "Documents/Backups/",
        "**/a/b/",
    )
    assert sd.translate_excludes(pats, "") == [
        ".Trash",
        "Library/Caches/",
        "node_modules",
        "*.gguf",
        "Documents/Backups/",
        "a/b/",
    ]
    assert sd.translate_excludes(pats, "Documents") == [
        ".Trash",
        "node_modules",
        "*.gguf",
        "Backups/",
    ]


def test_roots_for(tmp_path: Path):
    roots = sd.roots_for(
        tmp_path, "/Users/x", ("Documents/", "Library/Application Support/"), "home"
    )
    assert [(r[0], r[1], r[2], r[3]) for r in roots] == [
        ("documents", tmp_path / "Documents", "/Users/x/Documents", "Documents"),
        (
            "library-application-support",
            tmp_path / "Library/Application Support",
            "/Users/x/Library/Application Support",
            "Library/Application Support",
        ),
    ]
    assert sd.roots_for(tmp_path, "/Users/x/Developer", (), "dev")[0][:3] == (
        "developer",
        tmp_path,
        "/Users/x/Developer",
    )


# ── run() with a fake docsync api ───────────────────────────────────────────


@dataclass
class _Spec:
    name: str
    host: str | None
    user: str = ""
    bind_ip: str | None = None
    via: str = ""
    paths: dict = field(default_factory=dict)
    hosts_tried: list = field(default_factory=list)


@dataclass
class _Root:
    name: str
    local_path: str
    peer_path: str = ""
    excludes: list | None = None


@dataclass
class _Req:
    roots: list
    peers: list
    conflict_policy: str = "newest"
    force_deletes: bool = False
    dry_run: bool = False
    log: object = None
    state_dir: object = None
    excludes: list = field(default_factory=lambda: [".DS_Store"])


class _Report:
    def render(self):
        return "── node-b [documents] @ mini via Thunderbolt\n══ TOTAL"


class _Result:
    error = ""
    exit_code = 0
    report = _Report()

    def __init__(self, req):
        self.req = req

    def to_dict(self):
        blocks = []
        for r in self.req.roots:
            for p in self.req.peers:
                blocks.append(
                    {
                        "peer": p.name,
                        "root": r.name,
                        "ok": True,
                        "error": "",
                        "deleted_local": 1,
                        "deleted_remote": 0,
                        "conflicts": [{"path": "a"}],
                        "deletes_skipped": 0,
                        "warnings": [],
                        "pushed_files": 2,
                        "pulled_files": 1,
                        "pushed_bytes": 20,
                        "pulled_bytes": 10,
                        "unchanged": 5,
                        "safetynet_files": 1,
                    }
                )
        return {"peers": blocks}


class FakeApi:
    SyncRequest = _Req
    RootSpec = _Root
    PeerSpec = _Spec

    def __init__(self):
        self.requests = []

    def resolve_peer(self, hosts, user="", name="peer", log=None):
        return hosts[0], "Thunderbolt" if hosts[0].startswith("10.42") else "LAN"

    def sync(self, req):
        self.requests.append(req)
        return _Result(req)


def test_run_maps_request_and_result(tmp_path: Path, monkeypatch):
    (tmp_path / "Documents").mkdir()
    (tmp_path / "Desktop").mkdir()
    monkeypatch.setattr(sd, "_local_ip_assigned", lambda ip: True)
    api = FakeApi()
    prog = _Prog()
    peers = [(_node("node-b", "10.42.0.2", "CM-B.local", "CM-B (3).local"), "mafoe@10.42.0.2")]
    res = sd.run(
        prog=prog,
        local_home=tmp_path,
        remote_home="/Users/a321",
        peers=peers,
        self_ip="10.42.0.3",
        via="tb",
        target="home",
        includes=("Documents/", "Desktop/", "Missing/"),
        excludes=("Documents/Backups/", "**/node_modules/"),
        conflict_policy="prefer-remote",
        dry_run=False,
        compare_only=False,
        default_user="a321",
        force_deletes=True,
        api=api,
        state_dir=tmp_path / "state",
    )
    req = api.requests[0]
    assert [r.name for r in req.roots] == ["documents", "desktop"]  # Missing/ skipped with a note
    assert any("skip root" in n for n in prog.notes)
    assert req.roots[0].peer_path == "/Users/a321/Documents"
    assert req.roots[0].excludes == [
        ".DS_Store",
        "*.app",
        "*.xcarchive",
        "Backups/",
        "node_modules",
    ]
    assert (req.conflict_policy, req.force_deletes, req.state_dir) == (
        "remote",
        True,
        tmp_path / "state",
    )
    spec = req.peers[0]
    assert (spec.host, spec.user, spec.bind_ip, spec.via) == (
        "10.42.0.2",
        "mafoe",
        "10.42.0.3",
        "Thunderbolt",
    )
    assert spec.hosts_tried == ["10.42.0.2", "CM-B.local"]
    p = res.peers[0]
    assert (p.ok, p.push_files, p.pull_files, p.equal, p.safetynet_backed_up, p.via) == (
        True,
        4,
        2,
        10,
        2,
        "tb",
    )
    assert "deleted 2, conflicts 2" in p.message
    assert res.strategy.startswith("docsync ")
    assert "── node-b [documents] @ mini via Thunderbolt" in prog.notes


def test_run_wifi_never_binds_and_skips_tb_ip(tmp_path: Path):
    (tmp_path / "Documents").mkdir()
    api = FakeApi()
    peers = [(_node("node-b", "10.42.0.2", "CM-B.local"), "a321@CM-B.local")]
    sd.run(
        prog=_Prog(),
        local_home=tmp_path,
        remote_home=str(tmp_path),
        peers=peers,
        self_ip="10.42.0.3",
        via="wifi",
        target="home",
        includes=("Documents/",),
        excludes=(),
        conflict_policy="newer",
        dry_run=True,
        compare_only=False,
        default_user="a321",
        api=api,
        state_dir=tmp_path / "s",
    )
    spec = api.requests[0].peers[0]
    assert (spec.host, spec.bind_ip, spec.via) == ("CM-B.local", None, "LAN")
    assert api.requests[0].dry_run is True


def test_run_no_existing_root(tmp_path: Path):
    with pytest.raises(CliError) as exc:
        sd.run(
            prog=_Prog(),
            local_home=tmp_path,
            remote_home="/x",
            peers=[],
            self_ip="",
            via="tb",
            target="home",
            includes=("Nope/",),
            excludes=(),
            conflict_policy="newer",
            dry_run=False,
            compare_only=False,
            default_user="a321",
            api=FakeApi(),
        )
    assert exc.value.exit_code == 1


def test_sync_tree_uses_ditto_when_selected(monkeypatch):
    from maccluster.services import sync_engine, sync_service

    called = {}
    monkeypatch.setattr(
        sync_service, "sync_home", lambda ctx, **kw: called.setdefault("kw", kw) or "ditto-result"
    )
    out = sync_engine.sync_tree(object(), engine="ditto", push_only=True, conflict_policy="newer")
    assert called["kw"]["push_only"] is True
    assert "engine" not in called["kw"] and "force_deletes" not in called["kw"]
    assert out == called["kw"] or out == "ditto-result"


def test_find_docsync_wheel_prefers_highest_version(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("MACCLUSTER_DOCSYNC_WHEEL", raising=False)
    dist = tmp_path / "src" / "dist"
    dist.mkdir(parents=True)
    for v in ("1.1.0", "1.10.0", "1.2.3"):
        (dist / f"docsync_lan-{v}-py3-none-any.whl").write_text("w")
    monkeypatch.setenv("MACCLUSTER_DOCSYNC_SRC", str(tmp_path / "src"))
    assert sd.find_docsync_wheel().name == "docsync_lan-1.10.0-py3-none-any.whl"
    monkeypatch.setenv("MACCLUSTER_DOCSYNC_SRC", str(tmp_path / "none"))
    assert sd.find_docsync_wheel() is None


def test_inject_script_checks_digest_before_installing():
    script = sd.inject_script(
        "/tmp/maccluster-install.abc/docsync_lan-1.1.0-py3-none-any.whl", "deadbeef"
    )
    assert script.index("shasum -a 256") < script.index("pipx inject --force maccluster")
    assert "deadbeef" in script and "digest mismatch" in script
    assert "import docsync" in script


def test_git_trees_are_left_out():
    assert sd.drop_git_trees(
        (
            "Documents/",
            "Developer/",
            "Developer/x/",
            ".ssh/",
            ".config/",
            ".config/fish/",
            ".ssh/keys/",
        )
    ) == (
        ("Documents/", ".config/fish/"),
        ("Developer/", "Developer/x/", ".ssh/", ".config/", ".ssh/keys/"),
    )


def test_sync_dev_defaults_to_ditto(monkeypatch):
    monkeypatch.setattr(sd, "docsync_version", lambda: "1.1.2")
    base = dict(
        push_only=False,
        pull_only=False,
        transport=None,
        conflict_policy="newer",
        force_icloud=False,
        identical=False,
        quick=False,
        max_files=None,
        max_bytes=None,
    )
    reasons = sd.ditto_only_reasons(**base, target="dev")
    assert reasons and "git" in reasons[0]
    assert sd.resolve_engine("auto", reasons)[0] == "ditto"
    assert sd.ditto_only_reasons(**base, target="home") == []


# ── deadline: docsync.watchdog armed around the docsync run ─────────────────


class _FakeWatchdog:
    EXIT_DEADLINE = 4

    def __init__(self):
        self.calls: list[tuple] = []

    def arm(self, seconds, on_expire):
        self.calls.append(("arm", seconds))
        return True

    def disarm(self):
        self.calls.append(("disarm",))

    def kill_descendants(self, grace=1.0):
        self.calls.append(("kill",))


def test_watchdog_armed_and_disarmed_with_default_timeout(monkeypatch):
    from maccluster.constants import TIMEOUT_SYNC
    from maccluster.services import sync_engine

    wd = _FakeWatchdog()
    monkeypatch.setattr(sync_engine, "_load_watchdog", lambda prog: wd)
    out = sync_engine.run_with_deadline(_Prog(), None, lambda: "result")
    assert out == "result"
    assert wd.calls == [("arm", TIMEOUT_SYNC), ("disarm",)]


def test_watchdog_disarmed_when_the_run_raises(monkeypatch):
    from maccluster.services import sync_engine

    wd = _FakeWatchdog()
    monkeypatch.setattr(sync_engine, "_load_watchdog", lambda prog: wd)

    def boom():
        raise CliError("peer failed", exit_code=1)

    with pytest.raises(CliError):
        sync_engine.run_with_deadline(_Prog(), 120.0, boom)
    assert wd.calls == [("arm", 120.0), ("disarm",)]


def test_no_watchdog_runs_without_deadline(monkeypatch):
    import builtins

    from maccluster.services import sync_engine

    real_import = builtins.__import__

    def no_watchdog(name, *a, **k):
        if name == "docsync.watchdog" or (name == "docsync" and a and "watchdog" in (a[2] or ())):
            raise ImportError("no watchdog")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", no_watchdog)
    monkeypatch.setattr(sd, "docsync_version", lambda: "1.1.3")
    prog = _Prog()
    assert sync_engine.run_with_deadline(prog, 60.0, lambda: 7) == 7
    assert "docsync 1.1.3 has no watchdog; no deadline" in prog.notes


def test_deadline_subprocess_exits_4(tmp_path):
    """Real docsync.watchdog: a run that sleeps 30 s with a 2 s timeout ends the process with 4."""
    import subprocess
    import sys
    import time

    pytest.importorskip("docsync.watchdog")
    code = (
        "import time\n"
        "from maccluster.services import sync_engine\n"
        "class P:\n"
        "    def note(self, t): print(t)\n"
        "sync_engine.run_with_deadline(P(), 2.0, lambda: time.sleep(30))\n"
    )
    t0 = time.monotonic()
    src = str(Path(__file__).resolve().parents[3] / "src")  # this checkout, not the installed copy
    env = {**__import__("os").environ, "PYTHONPATH": src}
    r = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=25, env=env
    )
    assert r.returncode == 4, r.stdout + r.stderr
    assert "deadline of 2 s exceeded" in r.stderr
    assert time.monotonic() - t0 < 15
