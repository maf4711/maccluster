"""Coordinated heal: local ensure, then remote `maccluster heal` over TB SSH."""

from __future__ import annotations

import os
from dataclasses import dataclass

from maccluster.app_factory import AppContext
from maccluster.cli.exit_codes import DEGRADED, OK, USAGE
from maccluster.constants import LAUNCH_AGENT_LABEL, TIMEOUT_GENERIC
from maccluster.domain.models import MutateResult
from maccluster.errors import CliError, DegradedError, PrivilegeError
from maccluster.services.config_service import load_and_bind_self
from maccluster.services.fleet_exec import FleetHopResult, iter_peers, run_on_peer
from maccluster.services.mutate_service import ensure_local

FLEET_HEAL_PAUSE_S = 2.0

REMOTE_HEAL_CMD = (
    'export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"; '
    "if ! command -v maccluster >/dev/null 2>&1; then "
    'echo "maccluster not on peer — remote-install"; exit 66; fi; '
    "maccluster heal"
)

REMOTE_KICKSTART_CMD = f"launchctl kickstart -k gui/$(id -u)/{LAUNCH_AGENT_LABEL}"


@dataclass(frozen=True)
class FleetHealReport:
    self_result: MutateResult | None
    hops: tuple[FleetHopResult, ...]
    together: bool
    summary: str
    self_degraded: bool = False


def reject_fleet_combo(
    *,
    fleet: bool,
    loop: bool,
    watchdog: bool,
    together: bool = False,
) -> None:
    if together and not fleet:
        raise CliError("heal --together requires --fleet", exit_code=USAGE)
    if fleet and (loop or watchdog):
        raise CliError(
            "heal --fleet cannot be combined with --loop or --watchdog",
            exit_code=USAGE,
        )


def exit_for_fleet_heal(report: FleetHealReport, *, dry_run: bool) -> int:
    if dry_run:
        return OK
    if _peer_problems(report.hops) or report.self_degraded:
        return DEGRADED
    return OK


def run_fleet_heal(
    ctx: AppContext,
    *,
    dry_run: bool = False,
    peer: str | None = None,
    together: bool = False,
) -> FleetHealReport:
    cfg, self_node = load_and_bind_self(ctx)
    self_result, self_degraded = _ensure_self(ctx, dry_run=dry_run)
    if not dry_run:
        ctx.clock.sleep(FLEET_HEAL_PAUSE_S)

    hops: list[FleetHopResult] = []
    for node in iter_peers(cfg, self_node, peer=peer):
        if dry_run:
            hop = FleetHopResult(
                node_id=node.id,
                peer_ip=str(node.ip),
                ok=True,
                exit_code=0,
                stdout="",
                stderr="",
                skipped=True,
                message="dry-run: would run maccluster heal",
            )
            if ctx.verbose:
                print(f"dry-run hop {node.id} ({node.ip}): {REMOTE_HEAL_CMD}", flush=True)
        else:
            raw = run_on_peer(
                ctx,
                self_ip=str(self_node.ip),
                node=node,
                remote=(REMOTE_HEAL_CMD,),
                timeout=30.0,
                connect_timeout=8,
            )
            hop = _interpret_heal_hop(raw)
        hops.append(hop)
        ctx.audit.record(
            "heal-fleet",
            "ok" if hop.ok else ("skipped" if hop.skipped else "fail"),
            node=hop.node_id,
            ip=hop.peer_ip,
        )

    if together and not dry_run:
        _kickstart_together(ctx, self_ip=str(self_node.ip), hops=hops, cfg_nodes=cfg.nodes)

    ok_n = sum(1 for h in hops if h.ok or (h.skipped and dry_run))
    summary = f"self={'degraded' if self_degraded else 'ok'} hops={ok_n}/{len(hops)}"
    return FleetHealReport(
        self_result=self_result,
        hops=tuple(hops),
        together=together,
        summary=summary,
        self_degraded=self_degraded,
    )


def _ensure_self(ctx: AppContext, *, dry_run: bool) -> tuple[MutateResult | None, bool]:
    try:
        return ensure_local(ctx, dry_run=dry_run), False
    except DegradedError as exc:
        details = exc.details if isinstance(exc.details, MutateResult) else None
        return details, True
    except PrivilegeError:
        raise


def _interpret_heal_hop(hop: FleetHopResult) -> FleetHopResult:
    text = f"{hop.stdout} {hop.stderr} {hop.message}"
    low = text.lower()
    if hop.exit_code == 66 or "maccluster not on peer" in text:
        return FleetHopResult(
            node_id=hop.node_id,
            peer_ip=hop.peer_ip,
            ok=False,
            exit_code=hop.exit_code,
            stdout=hop.stdout,
            stderr=hop.stderr,
            skipped=True,
            message="maccluster not on peer — remote-install",
        )
    if "admin/sudo required" in low:
        return FleetHopResult(
            node_id=hop.node_id,
            peer_ip=hop.peer_ip,
            ok=False,
            exit_code=hop.exit_code or 1,
            stdout=hop.stdout,
            stderr=hop.stderr,
            skipped=False,
            message=f"run sudo maccluster heal on {hop.node_id}",
        )
    return hop


def _peer_problems(hops: tuple[FleetHopResult, ...] | list[FleetHopResult]) -> bool:
    for hop in hops:
        if hop.skipped and "remote-install" in hop.message:
            return True
        if not hop.ok and not hop.skipped:
            return True
        if not hop.ok and hop.skipped and hop.message == "ssh not found":
            return True
    return False


def _kickstart_together(ctx: AppContext, *, self_ip: str, hops, cfg_nodes) -> None:
    uid = os.getuid()
    domain = f"gui/{uid}/{LAUNCH_AGENT_LABEL}"
    try:
        ctx.runner.run(
            ["launchctl", "kickstart", "-k", domain],
            timeout=TIMEOUT_GENERIC,
        )
    except CliError:
        pass
    by_id = {n.id: n for n in cfg_nodes}
    for hop in hops:
        if hop.skipped and "remote-install" in hop.message:
            continue
        node = by_id.get(hop.node_id)
        if node is None:
            continue
        run_on_peer(
            ctx,
            self_ip=self_ip,
            node=node,
            remote=(REMOTE_KICKSTART_CMD,),
            timeout=8.0,
            connect_timeout=4,
        )
