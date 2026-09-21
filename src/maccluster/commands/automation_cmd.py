"""CLI rendering for package updates and unattended maintenance."""

from __future__ import annotations

from maccluster.app_factory import AppContext
from maccluster.errors import CliError
from maccluster.render.json_out import dumps, to_jsonable
from maccluster.render.plain import render_service
from maccluster.services import service_mgmt
from maccluster.services.automation_service import read_last_receipt, run_automation, run_update
from maccluster.services.package_update import DEFAULT_SOURCE


def _options(args) -> dict:
    return {
        "source": getattr(args, "source", None) or DEFAULT_SOURCE,
        "expected_sha256": getattr(args, "expected_sha256", None),
        "local_only": bool(getattr(args, "local_only", False)),
        "peer": getattr(args, "peer", None),
        "no_update": bool(getattr(args, "no_update", False)),
        "timeout": 600 if getattr(args, "timeout", None) is None else args.timeout,
        "sync": bool(getattr(args, "sync", False)),
    }


def run(ctx: AppContext, args) -> int:
    command = getattr(args, "command", "automation")
    action = getattr(args, "automation_action", None)
    if command == "update":
        options = _options(args)
        report = run_update(
            ctx,
            source=options["source"],
            timeout=options["timeout"],
            dry_run=bool(args.dry_run),
            expected_sha256=options["expected_sha256"],
        )
    elif action == "run":
        options = _options(args)
        if getattr(args, "saved", False):
            if (
                any(
                    getattr(args, key, None)
                    for key in (
                        "source",
                        "local_only",
                        "peer",
                        "no_update",
                        "sync",
                        "expected_sha256",
                    )
                )
                or getattr(args, "timeout", None) is not None
            ):
                raise CliError(
                    "--saved cannot be combined with explicit maintenance options", exit_code=2
                )
            options = service_mgmt.load_automation_settings(ctx)
        report = run_automation(ctx, **options, dry_run=bool(getattr(args, "dry_run", False)))
    else:
        if action == "install":
            state = service_mgmt.install_automation_service(
                ctx, interval_seconds=getattr(args, "interval", 86400), **_options(args)
            )
        elif action == "uninstall":
            state = service_mgmt.uninstall_automation_service(ctx)
        elif action == "status":
            state = service_mgmt.automation_service_status(ctx)
        else:
            raise CliError("automation requires run|install|status|uninstall", exit_code=2)
        last = read_last_receipt(ctx) if action == "status" else None
        if ctx.json_mode:
            print(dumps("automation", {"service": to_jsonable(state), "last_run": last}))
        else:
            print(render_service(state))
            if last:
                print(f"last run: {last.get('finished_at', '?')} exit={last.get('exit_code', '?')}")
        return (
            0
            if action != "install" or (state.installed and "failed" not in state.detail.lower())
            else 1
        )
    if ctx.json_mode:
        print(dumps(command, to_jsonable(report)))
    else:
        print(
            f"{command}: {'planned' if report.dry_run else 'OK' if report.ok else 'needs attention'}"
        )
        for step in report.steps:
            print(f"  [{step.status}] {step.target}: {step.name} — {step.message}")
        if report.receipt_path:
            print(f"receipt: {report.receipt_path}")
    return report.exit_code
