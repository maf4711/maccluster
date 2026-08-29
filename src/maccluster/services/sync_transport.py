"""Transfer stage of the sync ladder: try ``rdma`` → ``tb`` → ``wifi`` per peer.

``sync_service.sync_home`` still does inventory and planning. This module owns
what happens to one peer's plan afterwards:

- ``select_transports`` asks the ladder (``transport_ladder``) which rungs are
  usable for the peer, honouring ``--transport``;
- ``run_transfer_ladder`` walks those rungs in order. ``rdma`` hands the plan
  to ``arep xfer`` (``sync_rdma``); ``tb`` and ``wifi`` are the existing
  ssh/scp/ditto push + pull (``tb`` bound to the bridge IP, ``wifi`` to the
  ``*.local`` target, never bound). A rung that raises — or returns rc ≠ 0 —
  logs exactly ``transport downgrade <from>→<to>: <reason>`` and the next rung
  continues with the *remaining* files: after a partial run both sides are
  re-stat'ed and the plan recomputed, so nothing is transferred twice.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from maccluster.app_factory import AppContext
from maccluster.domain.models import DEFAULT_TRANSPORT_PRIORITY, TRANSPORT_NAMES, Node
from maccluster.errors import CliError
from maccluster.render.progress import NullProgress, ProgressLike, format_bytes
from maccluster.services.sync_rdma import run_rdma_transfer
from maccluster.services.sync_replan import replan_remaining
from maccluster.services.transport_ladder import (
    TransportFailed,
    TransportProbe,
    arep_status_json,
    choose_transports,
    probe_transports,
)

if TYPE_CHECKING:
    from maccluster.services.sync_service import FileMeta

__all__ = [
    "NO_TRANSFER",
    "TransferOutcome",
    "TransferPlan",
    "TransferTarget",
    "TransportChoice",
    "downgrade_line",
    "normalize_transport",
    "replan_remaining",
    "run_transfer_ladder",
    "select_transports",
]

SshTransfer = Callable[..., tuple[int, str, str, int]]  # sync_service._transfer_push/_pull
RdmaTransfer = Callable[..., int]  # sync_rdma.run_rdma_transfer
_REASON_MAX = 160

# --- data ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TransportChoice:
    """Rungs to try for one peer, in order; ``detail`` says why some are missing."""

    rungs: tuple[str, ...]
    detail: str = ""
    probe: TransportProbe | None = None


@dataclass(frozen=True)
class TransferPlan:
    """Output of ``plan_transfers`` (+ batch limits) for one peer."""

    to_push: Sequence[str]
    to_pull: Sequence[str]
    push_sizes: Mapping[str, int]
    pull_sizes: Mapping[str, int]
    local_inv: Mapping[str, FileMeta]
    remote_inv: Mapping[str, FileMeta]
    policy: str = "newer"

    @property
    def push_bytes(self) -> int:
        return sum(int(self.push_sizes.get(r, 0) or 0) for r in self.to_push)

    @property
    def pull_bytes(self) -> int:
        return sum(int(self.pull_sizes.get(r, 0) or 0) for r in self.to_pull)

    @property
    def total_bytes(self) -> int:
        return self.push_bytes + self.pull_bytes


@dataclass(frozen=True)
class TransferTarget:
    """Where one peer's bytes go: TB ssh target (+bind) and the Wi-Fi fallback."""

    node: Node
    ssh_target: str  # target the inventory ran on (TB, or .local on the wifi pass)
    bind_ip: str | None  # self TB IP for the tb rung; None on the wifi pass
    wifi_target: str | None  # user@host.local for the wifi rung
    local_home: Path
    remote_home: str

    def ssh_for(self, rung: str) -> tuple[str, str | None]:
        """(ssh_target, bind_ip) for *rung*: wifi is never bound to the bridge."""
        if rung == "wifi":
            return (self.wifi_target or self.ssh_target), None
        return self.ssh_target, self.bind_ip


@dataclass(frozen=True)
class TransferOutcome:
    """What the ladder achieved for one peer (same shape ``sync_home`` reported before)."""

    transport: str  # rung that ran last ("" when none was available)
    push_rc: int
    pull_rc: int
    push_stdout: str = ""
    pull_stdout: str = ""
    push_stderr: str = ""
    pull_stderr: str = ""
    push_bytes_done: int = 0
    pull_bytes_done: int = 0
    downgrades: tuple[str, ...] = ()  # exact log lines, in order
    messages: tuple[str, ...] = ()  # for SyncPeerResult.message


NO_TRANSFER = TransferOutcome(transport="", push_rc=0, pull_rc=0)


@dataclass
class _Rung:
    """Mutable per-rung result; ``reason`` set ⇒ the rung failed."""

    push: tuple[int, str, str, int] = (0, "", "", 0)
    pull: tuple[int, str, str, int] = (0, "", "", 0)
    reason: str | None = None
    moved: bool = False
    push_done: bool = False
    pull_done: bool = False


# --- helpers ---------------------------------------------------------------------------


def downgrade_line(frm: str, to: str, reason: str) -> str:
    return f"transport downgrade {frm}→{to}: {reason}"


def normalize_transport(transport: str | None) -> str | None:
    """Lower-cased rung name or None; unknown names are a usage error (exit 2)."""
    if transport is None:
        return None
    name = str(transport).strip().lower()
    if not name:
        return None
    if name not in TRANSPORT_NAMES:
        raise CliError(
            f"invalid --transport {transport!r} (use {', '.join(DEFAULT_TRANSPORT_PRIORITY)})",
            exit_code=2,
        )
    return name


def _first_line(text: str) -> str:
    for line in (text or "").splitlines():
        if line.strip():
            return line.strip()[:_REASON_MAX]
    return ""


def _fail_reason(exc: BaseException) -> str:
    if isinstance(exc, TransportFailed):
        return exc.reason
    return f"{type(exc).__name__}: {exc}"[:_REASON_MAX]


def _tools(ctx: AppContext) -> tuple[str, str, str]:
    return ctx.runner.resolve("ditto"), ctx.runner.resolve("ssh"), ctx.runner.resolve("scp")


# --- selection -------------------------------------------------------------------------


def select_transports(
    target: TransferTarget,
    ctx: AppContext,
    *,
    via: str,
    priority: Sequence[str],
    override: str | None = None,
    arep_status: Callable[[], dict | None] | None = None,
    tb_ping: Callable[[str], bool] | None = None,
) -> TransportChoice:
    """Ladder for one peer. The Wi-Fi pass (``via="wifi"``) is always just ``wifi``.

    An *override* that is unavailable yields no rungs; ``detail`` carries the
    reason so the peer row can show it. Never raises for probe failures.
    """
    override = normalize_transport(override)
    if via == "wifi":
        if override not in (None, "wifi"):
            raise CliError(
                f"--transport {override} cannot be combined with the wifi pass", exit_code=2
            )
        return TransportChoice(rungs=("wifi",), detail="wifi pass")
    # The inventory behind this plan already ran over target.ssh_target on the
    # bridge, so tb is proven reachable; ICMP (often firewalled on a peer) must
    # never veto the rung that just worked.
    probe = probe_transports(
        target.node,
        ctx,
        arep_status=arep_status or arep_status_json,
        tb_ping=tb_ping or (lambda _ip: True),
        wifi_target=lambda _node: target.wifi_target,
    )
    try:
        rungs = tuple(choose_transports(probe, tuple(priority), override))
    except TransportFailed as exc:
        return TransportChoice(rungs=(), detail=exc.reason, probe=probe)
    except ValueError as exc:
        raise CliError(str(exc), exit_code=2) from exc
    skipped = [f"{n}: {probe.reason(n)}" for n in priority if n not in rungs]
    return TransportChoice(rungs=rungs, detail="; ".join(skipped), probe=probe)


# --- rungs ----------------------------------------------------------------------------


def _run_ssh_rung(
    ctx: AppContext,
    rung: str,
    plan: TransferPlan,
    target: TransferTarget,
    *,
    dry_run: bool,
    timeout: float,
    work: Path,
    prog: ProgressLike,
    stream: bool,
    push: SshTransfer,
    pull: SshTransfer,
    push_only: bool,
    pull_only: bool,
    stop_on_fail: bool,
) -> _Rung:
    """Existing ssh/scp/ditto path; ``tb`` binds the bridge IP, ``wifi`` never does."""
    r = _Rung()
    ssh_target, bind_ip = target.ssh_for(rung)
    abs_ditto, abs_ssh, abs_scp = _tools(ctx)
    common = dict(
        abs_ditto=abs_ditto,
        abs_ssh=abs_ssh,
        abs_scp=abs_scp,
        ssh_target=ssh_target,
        local_home=target.local_home,
        remote_home=target.remote_home,
        dry_run=dry_run,
        timeout=timeout,
        work=work,
        progress=prog,
        bytes_total=plan.total_bytes,
        bind_ip=bind_ip,
    )
    prog.update(transport=rung)
    try:
        if not pull_only:
            r.push = push(
                ctx,
                rels=list(plan.to_push),
                sizes=dict(plan.push_sizes),
                bytes_base=0,
                stream=stream,
                **common,
            )
            r.moved = r.moved or r.push[3] > 0
            if r.push[0] != 0:
                r.reason = (
                    f"push rc={r.push[0]}: {_first_line(r.push[2] or r.push[1]) or 'no stderr'}"
                )
                if stop_on_fail:
                    return r
            else:
                r.push_done = True
        if not push_only:
            r.pull = pull(
                ctx,
                rels=list(plan.to_pull),
                sizes=dict(plan.pull_sizes),
                bytes_base=plan.push_bytes,
                **common,
            )
            r.moved = r.moved or r.pull[3] > 0
            if r.pull[0] != 0:
                r.reason = r.reason or (
                    f"pull rc={r.pull[0]}: {_first_line(r.pull[2] or r.pull[1]) or 'no stderr'}"
                )
            else:
                r.pull_done = True
    except Exception as exc:  # a broken rung must never abort the whole peer
        r.reason = _fail_reason(exc)
        r.moved = True  # unknown how far it got → re-stat before the next rung
        if not r.push_done:
            r.push = (1, "", r.reason, r.push[3])
        else:
            r.pull = (1, "", r.reason, r.pull[3])
    return r


def _run_rdma_rung(
    plan: TransferPlan,
    target: TransferTarget,
    *,
    timeout: float,
    prog: ProgressLike,
    rdma: RdmaTransfer,
    push_only: bool,
    pull_only: bool,
) -> _Rung:
    """Hand the plan to ``arep xfer push`` then ``pull``; progress feeds the bar."""
    r = _Rung()
    base = 0
    steps = (
        ("push", () if pull_only else plan.to_push, plan.local_inv),
        ("pull", () if push_only else plan.to_pull, plan.remote_inv),
    )
    for direction, rels, inv in steps:
        if not rels:
            continue
        prog.phase(
            "transfer", direction=direction, detail=f"{target.node.id} arep", transport="rdma"
        )

        def on_progress(done: int, total: int, _base: int = base, _dir: str = direction) -> None:
            r.moved = r.moved or done > 0
            prog.update(
                phase="transfer",
                direction=_dir,
                bytes_done=_base + done,
                bytes_total=plan.total_bytes or (_base + total),
                transport="rdma",
            )

        try:
            moved = int(
                rdma(
                    node_id=target.node.id,
                    direction=direction,
                    rels=list(rels),
                    inv=inv,
                    on_progress=on_progress,
                    timeout=timeout,
                )
            )
        except Exception as exc:
            r.reason = _fail_reason(exc)
            # arep ran and died (partial) ⇒ bytes may be on the peer → re-stat first
            r.moved = r.moved or bool(getattr(exc, "partial", False))
            setattr(r, direction, (1, "", r.reason, 0))
            return r
        base += moved
        r.moved = r.moved or moved > 0  # a silent arep (no progress events) still moved bytes
        out = f"{direction}: {len(rels)} files ({format_bytes(moved)}) via rdma"
        setattr(r, direction, (0, out, "", moved))
        setattr(r, f"{direction}_done", True)
    return r


def _outcome(rung: str, r: _Rung, downgrades: Sequence[str]) -> TransferOutcome:
    messages = list(downgrades)
    for direction, res in (("push", r.push), ("pull", r.pull)):
        rc, out = res[0], res[1]
        if rc != 0:
            messages.append(f"{direction} failed rc={rc}")
        elif out:
            messages.append(out.split("\n", 1)[0])
    if r.reason:
        messages.append(f"transport {rung} failed: {r.reason}")
    return TransferOutcome(
        transport=rung,
        push_rc=r.push[0],
        pull_rc=r.pull[0],
        push_stdout=r.push[1],
        pull_stdout=r.pull[1],
        push_stderr=r.push[2],
        pull_stderr=r.pull[2],
        push_bytes_done=r.push[3],
        pull_bytes_done=r.pull[3],
        downgrades=tuple(downgrades),
        messages=tuple(messages),
    )


# --- entry point --------------------------------------------------------------------


def run_transfer_ladder(
    ctx: AppContext,
    *,
    choice: TransportChoice | Sequence[str],
    plan: TransferPlan,
    target: TransferTarget,
    dry_run: bool,
    timeout: float,
    work: Path,
    progress: ProgressLike | None = None,
    stream: bool = True,
    push_only: bool = False,
    pull_only: bool = False,
    ssh_push: SshTransfer | None = None,
    ssh_pull: SshTransfer | None = None,
    rdma_xfer: RdmaTransfer | None = None,
) -> TransferOutcome:
    """Run the plan over the first rung that works, downgrading on failure.

    Dry runs never spawn arep: the first rung is reported and the existing
    ssh dry-run summaries are produced. Returns the existing failure shape
    (rc ≠ 0 + stderr) when every rung failed, or rc −1 when none was usable.
    """
    prog: ProgressLike = progress if progress is not None else NullProgress()
    detail = choice.detail if isinstance(choice, TransportChoice) else ""
    rungs = tuple(choice.rungs) if isinstance(choice, TransportChoice) else tuple(choice)
    if not rungs:
        msg = "no transport available" + (f": {detail}" if detail else "")
        prog.note(f"  {msg}")
        return TransferOutcome(
            transport="", push_rc=-1, pull_rc=-1, push_stderr=msg, pull_stderr=msg, messages=(msg,)
        )
    if ssh_push is None or ssh_pull is None:
        from maccluster.services.sync_service import _transfer_pull, _transfer_push

        ssh_push, ssh_pull = ssh_push or _transfer_push, ssh_pull or _transfer_pull
    rdma = rdma_xfer or run_rdma_transfer
    downgrades: list[str] = []
    for i, rung in enumerate(rungs):
        nxt = rungs[i + 1] if i + 1 < len(rungs) else None
        prog.note(f"  transport={rung} → {target.node.id}")
        if rung == "rdma" and not dry_run:
            r = _run_rdma_rung(
                plan,
                target,
                timeout=timeout,
                prog=prog,
                rdma=rdma,
                push_only=push_only,
                pull_only=pull_only,
            )
        else:
            r = _run_ssh_rung(
                ctx,
                rung,
                plan,
                target,
                dry_run=dry_run,
                timeout=timeout,
                work=work,
                prog=prog,
                stream=stream,
                push=ssh_push,
                pull=ssh_pull,
                push_only=push_only,
                pull_only=pull_only,
                stop_on_fail=nxt is not None,
            )
        if r.reason is None or nxt is None:
            return _outcome(rung, r, downgrades)
        line = downgrade_line(rung, nxt, r.reason)
        prog.note(line)
        downgrades.append(line)
        if r.moved and not dry_run:
            plan = replan_remaining(
                ctx, plan, target, rung=nxt, timeout=timeout, work=work, progress=prog
            )
    raise AssertionError("unreachable: ladder loop always returns")
