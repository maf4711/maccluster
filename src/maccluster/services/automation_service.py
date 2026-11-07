"""Unattended cluster maintenance, with isolated steps and one wheel per run."""

from __future__ import annotations

import json
import tempfile
from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from maccluster.app_factory import AppContext
from maccluster.errors import CliError, DegradedError
from maccluster.render.json_out import to_jsonable
from maccluster.services.config_service import load_and_bind_self
from maccluster.services.doctor_service import run_doctor
from maccluster.services.fleet_exec import iter_peers
from maccluster.services.fleet_heal_service import exit_for_fleet_heal, run_fleet_heal
from maccluster.services.mutate_service import ensure_local
from maccluster.services.package_update import (
    DEFAULT_SOURCE,
    build_wheel,
    install_wheel,
    validate_expected_sha256,
    validate_source,
    validate_timeout,
)
from maccluster.services.remote_install_service import remote_install
from maccluster.services.service_mgmt import install_service
from maccluster.services.sync_service import exit_code_for_sync, sync_home


@dataclass(frozen=True)
class AutomationStep:
    name: str
    target: str
    status: str
    message: str
    data: Any = None


@dataclass
class AutomationReport:
    command: str
    dry_run: bool
    source: str
    started_at: str
    finished_at: str = ""
    steps: list[AutomationStep] = field(default_factory=list)
    exit_code: int = 0
    ok: bool = True
    receipt_path: str | None = None
    expected_sha256: str | None = None


def receipt_path() -> Path:
    return Path.home() / "Library/Caches/maccluster/automation-last.json"


def read_last_receipt(ctx: AppContext) -> dict | None:
    try:
        value = json.loads(ctx.fs.read_text(receipt_path()))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _finish(ctx: AppContext, report: AutomationReport, *, persist: bool) -> AutomationReport:
    report.finished_at = ctx.clock.now().isoformat()
    report.exit_code = (
        1
        if any(s.status == "failed" for s in report.steps)
        else (3 if any(s.status == "degraded" for s in report.steps) else 0)
    )
    report.ok = report.exit_code == 0
    if persist:
        report.receipt_path = str(receipt_path())
        try:
            ctx.fs.write_text_atomic(
                receipt_path(),
                json.dumps(to_jsonable(report), indent=2) + "\n",
                mode=0o600,
            )
        except Exception as exc:
            report.steps.append(AutomationStep("receipt", "local", "failed", str(exc)))
            report.exit_code, report.ok, report.receipt_path = 1, False, None
    return report


def _attempt(
    report: AutomationReport,
    name: str,
    target: str,
    operation: Callable,
    *,
    evaluate: Callable | None = None,
    details: Callable | None = None,
):
    try:
        result = operation()
        status, message = (
            evaluate(result) if evaluate else ("ok", getattr(result, "message", "completed"))
        )
        report.steps.append(
            AutomationStep(name, target, status, message, details(result) if details else None)
        )
        return result
    except DegradedError as exc:
        report.steps.append(AutomationStep(name, target, "degraded", str(exc)))
    except Exception as exc:
        report.steps.append(AutomationStep(name, target, "failed", str(exc)))
    return None


def _code_status(code: int) -> str:
    return "ok" if code == 0 else "degraded" if code == 3 else "failed"


def _artifact_details(artifact) -> dict:
    return {"version": artifact.version, "sha256": artifact.sha256, "wheel": artifact.path.name}


def _service_status(state) -> tuple[str, str]:
    if not state.installed:
        return "failed", state.detail or "heal service is not installed"
    if "failed" in state.detail.lower():
        return "degraded", state.detail
    return "ok", state.detail or "heal and watchdog installed"


def _report(ctx: AppContext, command: str, source: str, dry_run: bool) -> AutomationReport:
    return AutomationReport(command, dry_run, source, ctx.clock.now().isoformat())


def _locked(ctx: AppContext, report: AutomationReport, operation: Callable) -> AutomationReport:
    try:
        # A dedicated lock serializes updates, without colliding with ensure_local's lock.
        with ctx.lock.acquire(Path.home() / ".config/maccluster/automation.lock", timeout=1):
            try:
                operation()
            except Exception as exc:
                report.steps.append(AutomationStep("automation", "local", "failed", str(exc)))
            return _finish(ctx, report, persist=True)
    except Exception as exc:
        report.steps.append(AutomationStep("lock", "local", "failed", str(exc)))
        # A competing run owns the last receipt; do not overwrite it.
        return _finish(ctx, report, persist=False)


def run_automation(
    ctx: AppContext,
    *,
    source: str = DEFAULT_SOURCE,
    expected_sha256: str | None = None,
    local_only: bool = False,
    peer: str | None = None,
    no_update: bool = False,
    dry_run: bool = False,
    timeout: float = 600,
    sync: bool = False,
) -> AutomationReport:
    """Maintain configured peers; local package replacement is always the final operation.

    ``no_update`` skips all package builds/installations, and heals existing peers.
    ``sync`` explicitly opts into verified home sync with overwrite backups.
    Peer update failures halt remaining updates and sync; local diagnostics still run.
    Dry runs read configuration and identity only: no network probes, locks or writes.
    """
    if local_only and (peer or sync):
        raise CliError("--local-only cannot be combined with --peer or --sync", exit_code=2)
    source = validate_source(source, require_exists=not no_update)
    expected_sha256 = validate_expected_sha256(source, expected_sha256)
    if no_update and expected_sha256 is not None:
        raise CliError("--sha256 cannot be combined with --no-update", exit_code=2)
    timeout = validate_timeout(timeout)
    report = _report(ctx, "automation", source, dry_run)
    report.expected_sha256 = expected_sha256

    def execute():
        selected = _attempt(
            report, "configuration", "local", lambda: _configured_peers(ctx, peer, local_only)
        )
        if selected is None:
            return
        if dry_run:
            _plan(report, selected, no_update=no_update, sync=sync)
            return
        _attempt(report, "heal", "local", lambda: ensure_local(ctx))
        _attempt(report, "service", "local", lambda: install_service(ctx), evaluate=_service_status)
        with ExitStack() as stack:
            artifact = None
            if not no_update:
                work = Path(
                    stack.enter_context(
                        tempfile.TemporaryDirectory(prefix="maccluster-automation-")
                    )
                )
                artifact = _attempt(
                    report,
                    "build",
                    "local",
                    lambda: build_wheel(
                        ctx,
                        source,
                        destination=work / "wheels",
                        timeout=timeout,
                        expected_sha256=expected_sha256,
                    ),
                    evaluate=lambda result: ("ok", f"built maccluster {result.version}"),
                    details=_artifact_details,
                )
            rollout_blocked = ""
            for node in selected:
                if no_update:
                    _attempt(
                        report,
                        "heal",
                        node.id,
                        lambda node=node: run_fleet_heal(ctx, peer=node.id),
                        evaluate=lambda result: (
                            _code_status(exit_for_fleet_heal(result, dry_run=False)),
                            result.summary,
                        ),
                    )
                elif rollout_blocked:
                    report.steps.append(
                        AutomationStep("remote-install", node.id, "skipped", rollout_blocked)
                    )
                elif artifact:
                    _attempt(
                        report,
                        "remote-install",
                        node.id,
                        lambda node=node: remote_install(
                            ctx,
                            node.id,
                            wheel_path=artifact.path,
                            copy_config=True,
                            preserve_config=True,
                            setup_ssh_config=False,
                            preflight_speedtest=False,
                            timeout=timeout,
                        ),
                        evaluate=lambda result: ("ok" if result.ok else "failed", result.message),
                    )
                    if report.steps[-1].status != "ok":
                        rollout_blocked = f"rollout stopped after unsuccessful peer {node.id}"
                else:
                    report.steps.append(
                        AutomationStep("remote-install", node.id, "skipped", "wheel build failed")
                    )
            if sync and rollout_blocked:
                report.steps.append(
                    AutomationStep("sync", peer or "all peers", "skipped", rollout_blocked)
                )
            elif sync:
                _attempt(
                    report,
                    "sync",
                    peer or "all peers",
                    lambda: sync_home(
                        ctx,
                        peer=peer,
                        safetynet=True,
                        verify=True,
                        no_speedtest=True,
                        timeout=timeout,
                    ),
                    evaluate=lambda result: (
                        _code_status(exit_code_for_sync(result)),
                        f"home sync completed for {len(result.peers)} peer(s)",
                    ),
                )
            _attempt(
                report,
                "doctor",
                "local",
                lambda: run_doctor(ctx),
                evaluate=lambda result: (
                    _code_status(result.exit_code),
                    f"{len(result.findings)} checks; exit={result.exit_code}",
                ),
                details=lambda result: to_jsonable(result.findings),
            )
            if rollout_blocked:
                report.steps.append(AutomationStep("update", "local", "skipped", rollout_blocked))
            elif artifact:
                _attempt(
                    report,
                    "update",
                    "local",
                    lambda: install_wheel(ctx, artifact, timeout=timeout),
                    details=to_jsonable,
                )
            elif not no_update:
                report.steps.append(
                    AutomationStep("update", "local", "skipped", "wheel build failed")
                )

    if dry_run:
        execute()
        return _finish(ctx, report, persist=False)
    return _locked(ctx, report, execute)


def _configured_peers(ctx: AppContext, peer: str | None, local_only: bool):
    cfg, self_node = load_and_bind_self(ctx)
    return () if local_only else iter_peers(cfg, self_node, peer=peer)


def _plan(report: AutomationReport, peers, *, no_update: bool, sync: bool) -> None:
    actions = [("heal", "local"), ("service", "local")]
    if not no_update:
        actions.append(("build", "local"))
    actions.extend(("heal" if no_update else "remote-install", node.id) for node in peers)
    if sync:
        actions.append(("sync", "selected peers"))
    actions.append(("doctor", "local"))
    if not no_update:
        actions.append(("update", "local"))
    for name, target in actions:
        condition = (
            "; only if preceding peer updates succeed"
            if not no_update and peers and name in ("remote-install", "sync", "update")
            else ""
        )
        report.steps.append(AutomationStep(name, target, "planned", f"would run {name}{condition}"))


def run_update(
    ctx: AppContext,
    *,
    source: str = DEFAULT_SOURCE,
    expected_sha256: str | None = None,
    dry_run: bool = False,
    timeout: float = 600,
) -> AutomationReport:
    """Update this installation independently of cluster configuration."""
    source = validate_source(source)
    expected_sha256 = validate_expected_sha256(source, expected_sha256)
    timeout = validate_timeout(timeout)
    report = _report(ctx, "update", source, dry_run)
    report.expected_sha256 = expected_sha256
    if dry_run:
        for name in ("build", "update"):
            report.steps.append(AutomationStep(name, "local", "planned", f"would run {name}"))
        return _finish(ctx, report, persist=False)

    def execute():
        with tempfile.TemporaryDirectory(prefix="maccluster-update-") as work:
            artifact = _attempt(
                report,
                "build",
                "local",
                lambda: build_wheel(
                    ctx,
                    source,
                    destination=Path(work) / "wheels",
                    timeout=timeout,
                    expected_sha256=expected_sha256,
                ),
                evaluate=lambda result: ("ok", f"built maccluster {result.version}"),
                details=_artifact_details,
            )
            if artifact:
                _attempt(
                    report,
                    "update",
                    "local",
                    lambda: install_wheel(ctx, artifact, timeout=timeout),
                    details=to_jsonable,
                )

    return _locked(ctx, report, execute)
