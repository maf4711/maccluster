"""Transfer stage on the transport ladder: rdma → tb → wifi with downgrade."""

from __future__ import annotations

import os
from collections.abc import Sequence
from ipaddress import IPv4Address
from pathlib import Path

import pytest

from maccluster.domain.models import Node, SyncPeerResult
from maccluster.errors import CliError
from maccluster.ports.process import ProcessResult
from maccluster.render.progress import NullProgress
from maccluster.services.sync_service import FileMeta
from maccluster.services.sync_transport import (
    TransferOutcome,
    TransferPlan,
    TransferTarget,
    TransportChoice,
    downgrade_line,
    normalize_transport,
    run_transfer_ladder,
    select_transports,
)
from maccluster.services.transport_ladder import TransportFailed

NODE_B = Node(
    id="node-b",
    hostnames=("mac-mini-b.local", "mac-mini-b"),
    ip=IPv4Address("10.42.0.2"),
    hw_uuid="00000000-0000-0000-0000-000000000002",
    ssh_target="a321@10.42.0.2",
)

AREP_STATUS = {
    "peers": [
        {
            "displayName": "mac-mini-b",
            "fingerprint": "SHA256:bbbb",
            "trust": "trusted",
            "transportCapable": ["rdma", "tcp"],
        }
    ]
}


class NoteProgress(NullProgress):
    """NullProgress that records note() lines and phase transports."""

    def __init__(self) -> None:
        self.notes: list[str] = []
        self.transports: list[str] = []

    def note(self, message: str) -> None:
        self.notes.append(message)

    def phase(
        self, name: str, *, direction: str = "", detail: str = "", transport: str = ""
    ) -> None:
        if transport:
            self.transports.append(transport)


class FakeSsh:
    """Stand-in for sync_service._transfer_push/_pull; scripted rc per ssh_target."""

    def __init__(self, rc_for: dict[str, int] | None = None) -> None:
        self.calls: list[dict] = []
        self.rc_for = rc_for or {}

    def __call__(self, ctx, **kw) -> tuple[int, str, str, int]:
        self.calls.append(kw)
        rels = list(kw["rels"])
        if not rels:
            return 0, "0 files", "", 0
        rc = self.rc_for.get(kw["ssh_target"], 0)
        payload = sum(kw["sizes"].get(r, 0) for r in rels)
        if kw.get("dry_run"):
            return 0, f"dry-run: {len(rels)} files", "", payload
        if rc != 0:
            return rc, "", f"ssh to {kw['ssh_target']} broke", 0
        return 0, f"{len(rels)} files ok", "", payload


class FakeRdma:
    """Stand-in for sync_rdma.run_rdma_transfer with per-direction scripts."""

    def __init__(self, script: dict[str, object] | None = None) -> None:
        self.calls: list[dict] = []
        self.script = script or {}

    def __call__(self, **kw) -> int:
        self.calls.append(kw)
        rels = list(kw["rels"])
        inv = kw["inv"]
        total = sum(inv[r].size for r in rels)
        action = self.script.get(kw["direction"], "ok")
        if isinstance(action, Exception):
            raise action
        if action == "partial-then-fail":
            first = inv[rels[0]].size
            kw["on_progress"](first, total)
            raise TransportFailed("rdma", "link lost")
        kw["on_progress"](total, total)
        return total


def _plan(push: dict[str, int] | None = None, pull: dict[str, int] | None = None) -> TransferPlan:
    push = push or {}
    pull = pull or {}
    local_inv = {r: FileMeta(mtime_ns=100, size=s) for r, s in push.items()}
    remote_inv = {r: FileMeta(mtime_ns=200, size=s) for r, s in pull.items()}
    return TransferPlan(
        to_push=tuple(sorted(push)),
        to_pull=tuple(sorted(pull)),
        push_sizes=dict(push),
        pull_sizes=dict(pull),
        local_inv=local_inv,
        remote_inv=remote_inv,
        policy="newer",
    )


def _target(tmp_path: Path, *, wifi: str | None = "a321@mac-mini-b.local") -> TransferTarget:
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    return TransferTarget(
        node=NODE_B,
        ssh_target="a321@10.42.0.2",
        bind_ip="10.42.0.1",
        wifi_target=wifi,
        local_home=home,
        remote_home=str(home),
    )


def _run(fake_ctx, tmp_path: Path, rungs: Sequence[str], **kw) -> TransferOutcome:
    work = tmp_path / "work"
    work.mkdir(exist_ok=True)
    args = dict(
        choice=TransportChoice(rungs=tuple(rungs)),
        plan=_plan(push={"a.txt": 5, "b.txt": 7}, pull={"c.txt": 3}),
        target=_target(tmp_path),
        dry_run=False,
        timeout=60.0,
        work=work,
        progress=NoteProgress(),
    )
    args.update(kw)
    return run_transfer_ladder(fake_ctx, **args)


# --- helpers ----------------------------------------------------------------------


def test_downgrade_line_format_is_exact():
    assert downgrade_line("rdma", "tb", "link lost") == "transport downgrade rdma→tb: link lost"


def test_normalize_transport_accepts_known_rejects_unknown():
    assert normalize_transport(None) is None
    assert normalize_transport(" RDMA ") == "rdma"
    with pytest.raises(CliError) as exc:
        normalize_transport("carrier-pigeon")
    assert exc.value.exit_code == 2


def test_target_ssh_for_rung_uses_wifi_without_bind(tmp_path: Path):
    tgt = _target(tmp_path)
    assert tgt.ssh_for("tb") == ("a321@10.42.0.2", "10.42.0.1")
    assert tgt.ssh_for("rdma") == ("a321@10.42.0.2", "10.42.0.1")
    assert tgt.ssh_for("wifi") == ("a321@mac-mini-b.local", None)


# --- direction attribution on exceptions (sync F13) -------------------------------


class RaisingSsh:
    """Raises for the direction named in *raise_on* ("push" | "pull")."""

    def __init__(self, raise_on: str) -> None:
        self.raise_on = raise_on
        self.calls: list[dict] = []

    def __call__(self, ctx, **kw) -> tuple[int, str, str, int]:
        self.calls.append(kw)
        rels = list(kw["rels"])
        if self.raise_on == "pull" and rels and rels[0] in kw["sizes"] and "c" in "".join(rels):
            raise OSError("pull side blew up")
        if self.raise_on == "push":
            raise OSError("push side blew up")
        return 0, "ok", "", sum(kw["sizes"].get(r, 0) for r in rels)


def test_pull_only_exception_is_attributed_to_pull_not_push(fake_ctx, tmp_path: Path):
    # With --pull-only the push step is skipped; an exception from the pull call
    # must not be recorded as a push failure with pull_rc left at 0.
    pull = RaisingSsh(raise_on="pull")
    out = _run(
        fake_ctx,
        tmp_path,
        ["wifi"],  # single rung so the failure is the final outcome
        ssh_push=FakeSsh(),
        ssh_pull=pull,
        pull_only=True,
    )
    assert out.push_rc == 0, "nothing was pushed under --pull-only"
    assert out.pull_rc == 1, "the failure belongs to the pull direction"
    assert "pull" in out.pull_stderr


def test_push_only_exception_is_attributed_to_push(fake_ctx, tmp_path: Path):
    push = RaisingSsh(raise_on="push")
    out = _run(
        fake_ctx,
        tmp_path,
        ["wifi"],
        ssh_push=push,
        ssh_pull=FakeSsh(),
        push_only=True,
    )
    assert out.push_rc == 1
    assert out.pull_rc == 0


# --- ladder iteration -------------------------------------------------------------


def test_rdma_first_rung_succeeds_and_ssh_is_never_used(fake_ctx, tmp_path: Path):
    push, pull, rdma = FakeSsh(), FakeSsh(), FakeRdma()
    prog = NoteProgress()
    out = _run(
        fake_ctx,
        tmp_path,
        ["rdma", "tb", "wifi"],
        ssh_push=push,
        ssh_pull=pull,
        rdma_xfer=rdma,
        progress=prog,
    )
    assert out.transport == "rdma"
    assert out.push_rc == 0 and out.pull_rc == 0
    assert out.push_bytes_done == 12 and out.pull_bytes_done == 3
    assert [c["direction"] for c in rdma.calls] == ["push", "pull"]
    assert rdma.calls[0]["node_id"] == "node-b"
    assert list(rdma.calls[0]["rels"]) == ["a.txt", "b.txt"]
    assert push.calls == [] and pull.calls == []
    assert out.downgrades == ()
    assert "rdma" in prog.transports
    assert any("transport=rdma" in n for n in prog.notes)


def test_rdma_failure_downgrades_to_tb_with_exact_log_line(fake_ctx, tmp_path: Path):
    push, pull = FakeSsh(), FakeSsh()
    rdma = FakeRdma({"push": TransportFailed("rdma", "arep exit 3: link lost")})
    prog = NoteProgress()
    out = _run(
        fake_ctx,
        tmp_path,
        ["rdma", "tb", "wifi"],
        ssh_push=push,
        ssh_pull=pull,
        rdma_xfer=rdma,
        progress=prog,
    )
    line = "transport downgrade rdma→tb: arep exit 3: link lost"
    assert line in prog.notes
    assert out.downgrades == (line,)
    assert out.transport == "tb"
    assert out.push_rc == 0 and out.pull_rc == 0
    # tb rung: existing ssh path with the TB target and the bridge bind IP
    assert push.calls[0]["ssh_target"] == "a321@10.42.0.2"
    assert push.calls[0]["bind_ip"] == "10.42.0.1"
    assert pull.calls[0]["bind_ip"] == "10.42.0.1"
    assert line in out.messages


def test_generic_exception_from_a_rung_also_downgrades(fake_ctx, tmp_path: Path):
    push, pull = FakeSsh(), FakeSsh()
    rdma = FakeRdma({"push": RuntimeError("boom")})
    prog = NoteProgress()
    out = _run(
        fake_ctx,
        tmp_path,
        ["rdma", "tb"],
        ssh_push=push,
        ssh_pull=pull,
        rdma_xfer=rdma,
        progress=prog,
    )
    assert out.transport == "tb"
    assert out.downgrades == ("transport downgrade rdma→tb: RuntimeError: boom",)


def test_tb_rc_failure_downgrades_to_wifi_without_bind(fake_ctx, tmp_path: Path):
    push = FakeSsh(rc_for={"a321@10.42.0.2": 255})
    pull = FakeSsh()
    prog = NoteProgress()
    out = _run(fake_ctx, tmp_path, ["tb", "wifi"], ssh_push=push, ssh_pull=pull, progress=prog)
    assert out.transport == "wifi"
    assert out.push_rc == 0 and out.pull_rc == 0
    assert len(out.downgrades) == 1
    assert out.downgrades[0].startswith("transport downgrade tb→wifi: push rc=255")
    assert push.calls[-1]["ssh_target"] == "a321@mac-mini-b.local"
    assert push.calls[-1]["bind_ip"] is None
    assert pull.calls[-1]["ssh_target"] == "a321@mac-mini-b.local"


def test_all_rungs_fail_keeps_existing_failure_path(fake_ctx, tmp_path: Path):
    push = FakeSsh(rc_for={"a321@10.42.0.2": 1, "a321@mac-mini-b.local": 1})
    pull = FakeSsh()
    rdma = FakeRdma({"push": TransportFailed("rdma", "no device")})
    out = _run(
        fake_ctx, tmp_path, ["rdma", "tb", "wifi"], ssh_push=push, ssh_pull=pull, rdma_xfer=rdma
    )
    assert out.transport == "wifi"
    assert out.push_rc == 1
    assert out.pull_rc == 0 or out.pull_rc == -1
    assert len(out.downgrades) == 2
    assert any("push failed rc=1" in m for m in out.messages)
    assert "broke" in out.push_stderr


def test_rdma_only_failure_reports_rc_and_reason(fake_ctx, tmp_path: Path):
    rdma = FakeRdma({"pull": TransportFailed("rdma", "peer aborted")})
    out = _run(fake_ctx, tmp_path, ["rdma"], rdma_xfer=rdma)
    assert out.transport == "rdma"
    assert out.push_rc == 0
    assert out.pull_rc != 0
    assert "peer aborted" in out.pull_stderr
    assert out.downgrades == ()
    assert any("peer aborted" in m for m in out.messages)


def test_no_rungs_available_fails_with_detail(fake_ctx, tmp_path: Path):
    out = _run(
        fake_ctx,
        tmp_path,
        [],
        choice=TransportChoice(rungs=(), detail="unavailable: arep peer trust=unpaired"),
    )
    assert out.transport == ""
    assert out.push_rc == -1 and out.pull_rc == -1
    assert any("unpaired" in m for m in out.messages)


def test_dry_run_never_spawns_arep(fake_ctx, tmp_path: Path):
    push, pull, rdma = FakeSsh(), FakeSsh(), FakeRdma()
    out = _run(
        fake_ctx,
        tmp_path,
        ["rdma", "tb"],
        dry_run=True,
        ssh_push=push,
        ssh_pull=pull,
        rdma_xfer=rdma,
    )
    assert rdma.calls == []
    assert out.transport == "rdma"
    assert push.calls[0]["dry_run"] is True
    assert out.push_rc == 0 and out.pull_rc == 0


def test_empty_plan_is_ok_on_first_rung(fake_ctx, tmp_path: Path):
    push, pull, rdma = FakeSsh(), FakeSsh(), FakeRdma()
    out = _run(
        fake_ctx,
        tmp_path,
        ["rdma", "tb"],
        plan=_plan(),
        ssh_push=push,
        ssh_pull=pull,
        rdma_xfer=rdma,
    )
    assert out.transport == "rdma"
    assert out.push_rc == 0 and out.pull_rc == 0
    assert rdma.calls == []


# --- no double transfer after a partial rdma run ------------------------------------


class RestatRunner:
    """Answers the remote re-stat (scp list + ssh python3) with a scripted inventory."""

    def __init__(self, remote_lines: str) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.remote_lines = remote_lines

    def resolve(self, basename: str) -> str:
        return f"/usr/bin/{basename}"

    def run(
        self, argv: Sequence[str], *, timeout: float = 15.0, check: bool = False
    ) -> ProcessResult:
        full = tuple(argv)
        self.calls.append(full)
        if Path(full[0]).name == "ssh" and "python3" in " ".join(full):
            return ProcessResult(argv=full, returncode=0, stdout=self.remote_lines, stderr="")
        return ProcessResult(argv=full, returncode=0, stdout="", stderr="")


def test_partial_rdma_push_does_not_resend_finished_files(fake_ctx, tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()
    (home / "a.txt").write_bytes(b"aaaaa")
    (home / "b.txt").write_bytes(b"bbbbbbb")
    st_a = os.lstat(home / "a.txt")
    st_b = os.lstat(home / "b.txt")
    local_inv = {
        "a.txt": FileMeta(mtime_ns=st_a.st_mtime_ns, size=st_a.st_size),
        "b.txt": FileMeta(mtime_ns=st_b.st_mtime_ns, size=st_b.st_size),
    }
    plan = TransferPlan(
        to_push=("a.txt", "b.txt"),
        to_pull=(),
        push_sizes={"a.txt": 5, "b.txt": 7},
        pull_sizes={},
        local_inv=local_inv,
        remote_inv={},
    )
    # After the partial run the peer already holds a.txt with identical meta.
    fake_ctx.runner = RestatRunner(f"a.txt\t{st_a.st_mtime_ns}\t{st_a.st_size}\n")
    push, pull = FakeSsh(), FakeSsh()
    rdma = FakeRdma({"push": "partial-then-fail"})
    prog = NoteProgress()
    out = _run(
        fake_ctx,
        tmp_path,
        ["rdma", "tb"],
        plan=plan,
        ssh_push=push,
        ssh_pull=pull,
        rdma_xfer=rdma,
        progress=prog,
    )
    assert out.transport == "tb"
    assert out.push_rc == 0
    assert [list(c["rels"]) for c in push.calls] == [["b.txt"]]
    assert push.calls[0]["sizes"] == {"b.txt": 7}
    assert "transport downgrade rdma→tb: link lost" in prog.notes
    # the re-stat went over the tb rung's ssh target with the bridge bind
    ssh_calls = [c for c in fake_ctx.runner.calls if Path(c[0]).name == "ssh"]
    assert ssh_calls and "BindAddress=10.42.0.1" in " ".join(ssh_calls[0])


def test_clean_rdma_failure_without_progress_skips_restat(fake_ctx, tmp_path: Path):
    fake_ctx.runner = RestatRunner("")
    push, pull = FakeSsh(), FakeSsh()
    rdma = FakeRdma({"push": TransportFailed("rdma", "cannot start arep")})
    out = _run(fake_ctx, tmp_path, ["rdma", "tb"], ssh_push=push, ssh_pull=pull, rdma_xfer=rdma)
    assert out.transport == "tb"
    assert fake_ctx.runner.calls == []
    assert [list(c["rels"]) for c in push.calls] == [["a.txt", "b.txt"]]


# --- transport selection ------------------------------------------------------------


def test_select_transports_orders_and_filters(fake_ctx, tmp_path: Path):
    choice = select_transports(
        _target(tmp_path),
        fake_ctx,
        via="tb",
        priority=("rdma", "tb", "wifi"),
        arep_status=lambda: AREP_STATUS,
    )
    assert choice.rungs == ("rdma", "tb", "wifi")
    assert choice.probe is not None and choice.probe.rdma_available
    no_arep = select_transports(
        _target(tmp_path, wifi=None),
        fake_ctx,
        via="tb",
        priority=("rdma", "tb", "wifi"),
        arep_status=lambda: None,
    )
    assert no_arep.rungs == ("tb",)
    assert "rdma" in no_arep.detail and "wifi" in no_arep.detail


def test_select_transports_honours_override(fake_ctx, tmp_path: Path):
    forced = select_transports(
        _target(tmp_path),
        fake_ctx,
        via="tb",
        priority=("rdma", "tb", "wifi"),
        override="tb",
        arep_status=lambda: AREP_STATUS,
    )
    assert forced.rungs == ("tb",)
    unavailable = select_transports(
        _target(tmp_path),
        fake_ctx,
        via="tb",
        priority=("rdma", "tb", "wifi"),
        override="rdma",
        arep_status=lambda: None,
    )
    assert unavailable.rungs == ()
    assert "unavailable" in unavailable.detail


def test_select_transports_wifi_pass_is_wifi_only(fake_ctx, tmp_path: Path):
    choice = select_transports(
        _target(tmp_path),
        fake_ctx,
        via="wifi",
        priority=("rdma", "tb", "wifi"),
        arep_status=lambda: AREP_STATUS,
    )
    assert choice.rungs == ("wifi",)
    with pytest.raises(CliError):
        select_transports(
            _target(tmp_path), fake_ctx, via="wifi", priority=("rdma", "tb"), override="rdma"
        )


# --- sync_home wiring ------------------------------------------------------------------


class SyncRunner:
    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []

    def resolve(self, basename: str) -> str:
        if basename in ("ssh", "scp", "ditto"):
            return f"/usr/bin/{basename}"
        raise CliError(f"tool not found: {basename}", exit_code=1)

    def run(
        self, argv: Sequence[str], *, timeout: float = 15.0, check: bool = False
    ) -> ProcessResult:
        full = tuple(argv)
        self.calls.append(full)
        if Path(full[0]).name in ("ssh", "scp", "ditto"):
            return ProcessResult(argv=full, returncode=0, stdout="", stderr="")
        return ProcessResult(argv=full, returncode=1, stdout="", stderr="unknown")

    def run_pipe(
        self, producer: Sequence[str], consumer: Sequence[str], *, timeout: float = 15.0
    ) -> ProcessResult:
        self.calls.append(tuple(producer) + ("|",) + tuple(consumer))
        return ProcessResult(argv=tuple(producer), returncode=0, stdout="", stderr="")


def _tree(tmp_path: Path) -> Path:
    tree = tmp_path / "Developer"
    (tree / "repo").mkdir(parents=True)
    (tree / "repo" / "a.py").write_text("print(1)\n", encoding="utf-8")
    return tree


def test_sync_home_records_rdma_transport_per_peer(fake_ctx, tmp_path: Path, monkeypatch):
    from maccluster.services import sync_transport
    from maccluster.services.sync_service import sync_home

    rdma = FakeRdma()
    monkeypatch.setattr(sync_transport, "arep_status_json", lambda *a, **k: AREP_STATUS)
    monkeypatch.setattr(sync_transport, "run_rdma_transfer", rdma)
    tree = _tree(tmp_path)
    fake_ctx.runner = SyncRunner()
    result = sync_home(
        fake_ctx,
        peer="node-b",
        home=tree,
        remote_home=str(tree),
        user="a321",
        timeout=60,
        no_speedtest=True,
        write_log=False,
        target="dev",
        includes=("repo",),
    )
    peer = result.peers[0]
    assert peer.ok, peer.message
    assert peer.transport == "rdma"
    assert peer.via == "tb"
    assert peer.push_files == 1
    assert [c["direction"] for c in rdma.calls] == ["push"]
    assert result.transport_priority == ("rdma", "tb", "wifi")


def test_sync_home_transport_override_skips_rdma(fake_ctx, tmp_path: Path, monkeypatch):
    from maccluster.services import sync_transport
    from maccluster.services.sync_service import sync_home

    rdma = FakeRdma()
    monkeypatch.setattr(sync_transport, "arep_status_json", lambda *a, **k: AREP_STATUS)
    monkeypatch.setattr(sync_transport, "run_rdma_transfer", rdma)
    tree = _tree(tmp_path)
    fake_ctx.runner = SyncRunner()
    result = sync_home(
        fake_ctx,
        peer="node-b",
        home=tree,
        remote_home=str(tree),
        user="a321",
        timeout=60,
        dry_run=True,
        no_speedtest=True,
        write_log=False,
        target="dev",
        includes=("repo",),
        transport="tb",
    )
    assert result.peers[0].transport == "tb"
    assert rdma.calls == []
    with pytest.raises(CliError) as exc:
        sync_home(fake_ctx, peer="node-b", home=tree, user="a321", transport="bogus")
    assert exc.value.exit_code == 2


def test_sync_home_transport_wifi_uses_local_hostname(fake_ctx, tmp_path: Path, monkeypatch):
    from maccluster.services import sync_transport
    from maccluster.services.sync_service import sync_home

    monkeypatch.setattr(sync_transport, "arep_status_json", lambda *a, **k: AREP_STATUS)
    tree = _tree(tmp_path)
    fake_ctx.runner = SyncRunner()
    result = sync_home(
        fake_ctx,
        peer="node-b",
        home=tree,
        remote_home=str(tree),
        user="a321",
        timeout=60,
        dry_run=True,
        no_speedtest=True,
        write_log=False,
        target="dev",
        includes=("repo",),
        transport="wifi",
    )
    peer = result.peers[0]
    assert peer.via == "wifi"
    assert peer.transport == "wifi"
    assert peer.ssh_target == "a321@mac-mini-b.local"
    joined = " ".join(" ".join(c) for c in fake_ctx.runner.calls)
    assert "BindAddress=" not in joined


def test_sync_home_downgrade_lands_in_peer_result(fake_ctx, tmp_path: Path, monkeypatch):
    from maccluster.services import sync_transport
    from maccluster.services.sync_service import sync_home

    rdma = FakeRdma({"push": TransportFailed("rdma", "link lost")})
    monkeypatch.setattr(sync_transport, "arep_status_json", lambda *a, **k: AREP_STATUS)
    monkeypatch.setattr(sync_transport, "run_rdma_transfer", rdma)
    tree = _tree(tmp_path)
    fake_ctx.runner = SyncRunner()
    result = sync_home(
        fake_ctx,
        peer="node-b",
        home=tree,
        remote_home=str(tree),
        user="a321",
        timeout=60,
        no_speedtest=True,
        write_log=False,
        target="dev",
        includes=("repo",),
    )
    peer = result.peers[0]
    assert peer.ok, peer.message
    assert peer.transport == "tb"
    assert peer.downgrades == ("transport downgrade rdma→tb: link lost",)
    assert "transport downgrade rdma→tb: link lost" in peer.message


# --- rendering -----------------------------------------------------------------------


def test_render_plain_shows_transport_and_downgrade():
    from maccluster.commands.sync_cmd import _render_plain
    from maccluster.domain.models import SyncHomeResult

    peer = SyncPeerResult(
        peer_id="node-b",
        peer_ip="10.42.0.2",
        ssh_target="a321@10.42.0.2",
        push_rc=0,
        pull_rc=0,
        ok=True,
        message="ok",
        transport="rdma",
        downgrades=("transport downgrade rdma→tb: link lost",),
    )
    result = SyncHomeResult(
        local_home="/tmp/x", dry_run=False, strategy="newer", peers=(peer,), target="dev"
    )
    out = _render_plain(result)
    assert "[tb]" in out
    assert "transport=rdma" in out
    assert "transport downgrade rdma→tb: link lost" in out


def test_sync_cmd_transport_flag_runs_single_pass(fake_ctx, tmp_path: Path, monkeypatch, capsys):
    from maccluster.cli.parser import build_parser
    from maccluster.commands import sync_cmd
    from maccluster.services import sync_transport

    monkeypatch.setattr(sync_transport, "arep_status_json", lambda *a, **k: None)
    monkeypatch.setattr(
        "maccluster.services.sync_history.write_run_log",
        lambda result, log_dir=None: tmp_path / "sync-log.json",
    )
    tree = _tree(tmp_path)
    fake_ctx.runner = SyncRunner()
    args = build_parser().parse_args(
        [
            "sync",
            "dev",
            "--home",
            str(tree),
            "--peer",
            "node-b",
            "--dry-run",
            "--no-speedtest",
            "--no-progress",
            "--no-mcprt",
            "--user",
            "a321",
            "--transport",
            "tb",
        ]
    )
    assert sync_cmd.run(fake_ctx, args) == 0
    out = capsys.readouterr().out
    assert "transport=tb" in out
    assert "[wifi]" not in out
