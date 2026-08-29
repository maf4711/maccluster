"""sync_transport hardening: no double transfer after partial rdma, tb never ICMP-vetoed."""

from __future__ import annotations

import os
from collections.abc import Sequence
from ipaddress import IPv4Address
from pathlib import Path

import pytest

from maccluster.domain.enums import ReachabilityState
from maccluster.domain.models import Node
from maccluster.ports.process import ProcessResult
from maccluster.render.progress import NullProgress
from maccluster.services.sync_service import FileMeta
from maccluster.services.sync_transport import (
    TransferPlan,
    TransferTarget,
    TransportChoice,
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
            "trust": "trusted",
            "transportCapable": ["rdma", "tcp"],
        }
    ]
}


class NoteProgress(NullProgress):
    def __init__(self) -> None:
        self.notes: list[str] = []

    def note(self, message: str) -> None:
        self.notes.append(message)


class FakeSsh:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def __call__(self, ctx, **kw) -> tuple[int, str, str, int]:
        self.calls.append(kw)
        rels = list(kw["rels"])
        payload = sum(kw["sizes"].get(r, 0) for r in rels)
        return 0, f"{len(rels)} files ok", "", payload


class RestatRunner:
    """Answers the remote re-stat with a scripted inventory; records every call."""

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


def _home_with(tmp_path: Path, names: dict[str, bytes]) -> tuple[Path, dict[str, FileMeta]]:
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    inv: dict[str, FileMeta] = {}
    for name, data in names.items():
        (home / name).write_bytes(data)
        st = os.lstat(home / name)
        inv[name] = FileMeta(mtime_ns=st.st_mtime_ns, size=st.st_size)
    return home, inv


def _target(home: Path, *, wifi: str | None = "a321@mac-mini-b.local") -> TransferTarget:
    return TransferTarget(
        node=NODE_B,
        ssh_target="a321@10.42.0.2",
        bind_ip="10.42.0.1",
        wifi_target=wifi,
        local_home=home,
        remote_home=str(home),
    )


def _lines(inv: dict[str, FileMeta], *names: str) -> str:
    return "".join(f"{n}\t{inv[n].mtime_ns}\t{inv[n].size}\n" for n in names)


# --- no double transfer -----------------------------------------------------------------


def test_partial_flag_without_progress_events_triggers_restat(fake_ctx, tmp_path: Path):
    home, inv = _home_with(tmp_path, {"a.txt": b"aaaaa", "b.txt": b"bbbbbbb"})
    plan = TransferPlan(("a.txt", "b.txt"), (), {"a.txt": 5, "b.txt": 7}, {}, inv, {})
    fake_ctx.runner = RestatRunner(_lines(inv, "a.txt"))
    push = FakeSsh()

    def rdma(**kw) -> int:  # arep died with rc≠0 and never printed progress
        raise TransportFailed("rdma", "arep exit 3: link lost", partial=True)

    out = run_transfer_ladder(
        fake_ctx,
        choice=TransportChoice(rungs=("rdma", "tb")),
        plan=plan,
        target=_target(home),
        dry_run=False,
        timeout=60.0,
        work=tmp_path / "work",
        progress=NoteProgress(),
        ssh_push=push,
        ssh_pull=FakeSsh(),
        rdma_xfer=rdma,
    )
    assert out.transport == "tb"
    assert fake_ctx.runner.calls, "peer must be re-stat'ed after a partial arep run"
    assert [list(c["rels"]) for c in push.calls] == [["b.txt"]]


def test_silent_push_success_then_pull_failure_does_not_resend_push(fake_ctx, tmp_path: Path):
    home, inv = _home_with(tmp_path, {"a.txt": b"aaaaa"})
    remote_inv = {"c.txt": FileMeta(mtime_ns=200, size=3)}
    plan = TransferPlan(("a.txt",), ("c.txt",), {"a.txt": 5}, {"c.txt": 3}, inv, remote_inv)
    fake_ctx.runner = RestatRunner(_lines(inv, "a.txt") + "c.txt\t200\t3\n")
    push, pull = FakeSsh(), FakeSsh()

    def rdma(**kw) -> int:  # push moves bytes but emits only a `done` event (no progress)
        if kw["direction"] == "push":
            return 5
        raise TransportFailed("rdma", "cannot start arep")

    out = run_transfer_ladder(
        fake_ctx,
        choice=TransportChoice(rungs=("rdma", "tb")),
        plan=plan,
        target=_target(home),
        dry_run=False,
        timeout=60.0,
        work=tmp_path / "work",
        progress=NoteProgress(),
        ssh_push=push,
        ssh_pull=pull,
        rdma_xfer=rdma,
    )
    assert out.transport == "tb"
    assert fake_ctx.runner.calls, "a rung that moved bytes must trigger a re-stat"
    assert [list(c["rels"]) for c in push.calls] == [[]]
    assert [list(c["rels"]) for c in pull.calls] == [["c.txt"]]


# --- tb never vetoed by ICMP -----------------------------------------------------------


def test_select_transports_keeps_tb_when_icmp_ping_fails(fake_ctx, tmp_path: Path):
    fake_ctx.reachability.states["10.42.0.2"] = ReachabilityState.DOWN
    home = tmp_path / "home"
    home.mkdir()
    choice = select_transports(
        _target(home),
        fake_ctx,
        via="tb",
        priority=("rdma", "tb", "wifi"),
        arep_status=lambda: AREP_STATUS,
        home_dir=home,  # rdma is only offered when the tree root is $HOME (sync F8)
    )
    assert choice.rungs == ("rdma", "tb", "wifi")
    forced = select_transports(
        _target(home),
        fake_ctx,
        via="tb",
        priority=("rdma", "tb", "wifi"),
        override="tb",
        arep_status=lambda: None,
        home_dir=home,
    )
    assert forced.rungs == ("tb",)


# --- reason hygiene / rung validation ------------------------------------------------------


def test_downgrade_reason_is_capped_and_sanitized(fake_ctx, tmp_path: Path):
    home, inv = _home_with(tmp_path, {"a.txt": b"aaaaa"})
    plan = TransferPlan(("a.txt",), (), {"a.txt": 5}, {}, inv, {})
    prog = NoteProgress()

    def rdma(**kw) -> int:
        raise TransportFailed("rdma", "\x1b[31mbad\x1b[0m " + "y" * 5000)

    out = run_transfer_ladder(
        fake_ctx,
        choice=TransportChoice(rungs=("rdma", "tb")),
        plan=plan,
        target=_target(home),
        dry_run=False,
        timeout=60.0,
        work=tmp_path / "work",
        progress=prog,
        ssh_push=FakeSsh(),
        ssh_pull=FakeSsh(),
        rdma_xfer=rdma,
    )
    line = out.downgrades[0]
    assert line.startswith("transport downgrade rdma→tb: bad")
    assert "\x1b" not in line
    assert len(line) < 260
    assert line in prog.notes


def test_run_transfer_ladder_rejects_unknown_rung(fake_ctx, tmp_path: Path):
    home, inv = _home_with(tmp_path, {"a.txt": b"aaaaa"})
    plan = TransferPlan(("a.txt",), (), {"a.txt": 5}, {}, inv, {})
    with pytest.raises(ValueError):
        run_transfer_ladder(
            fake_ctx,
            choice=("usb",),
            plan=plan,
            target=_target(home),
            dry_run=True,
            timeout=60.0,
            work=tmp_path / "work",
            ssh_push=FakeSsh(),
            ssh_pull=FakeSsh(),
        )
