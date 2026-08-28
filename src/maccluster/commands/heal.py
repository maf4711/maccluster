"""heal / heal --loop / heal --fleet."""

from __future__ import annotations

from maccluster.app_factory import AppContext
from maccluster.cli.exit_codes import OK
from maccluster.errors import DegradedError
from maccluster.render.json_out import dumps, to_jsonable
from maccluster.render.plain import render_fleet_heal
from maccluster.services.fleet_heal_service import (
    exit_for_fleet_heal,
    reject_fleet_combo,
    run_fleet_heal,
)
from maccluster.services.heal_loop_service import run_heal_loop
from maccluster.services.mutate_service import ensure_local


def run(ctx: AppContext, args) -> int:
    fleet = bool(getattr(args, "fleet", False))
    together = bool(getattr(args, "together", False))
    reject_fleet_combo(
        fleet=fleet,
        loop=bool(getattr(args, "loop", False)),
        watchdog=bool(getattr(args, "watchdog", False)),
        together=together,
    )

    if fleet:
        dry_run = bool(getattr(args, "dry_run", False))
        report = run_fleet_heal(
            ctx,
            dry_run=dry_run,
            peer=getattr(args, "peer", None),
            together=together,
        )
        if ctx.json_mode:
            print(dumps("heal", to_jsonable(report)))
        else:
            print(render_fleet_heal(report, dry_run=dry_run))
        return exit_for_fleet_heal(report, dry_run=dry_run)

    if getattr(args, "watchdog", False):
        from maccluster.services.heal_loop_service import run_heal_watchdog

        return run_heal_watchdog(ctx)

    if getattr(args, "loop", False):
        interval = getattr(args, "interval", None)
        # best-effort loop — not HA
        return run_heal_loop(ctx, interval=interval)

    try:
        result = ensure_local(ctx)
    except DegradedError as exc:
        if ctx.json_mode:
            print(dumps("heal", {"degraded": True, "message": exc.message}))
        else:
            print(exc.message)
        return exc.exit_code
    if ctx.json_mode:
        print(dumps("heal", to_jsonable(result)))
    else:
        print(result.message)
    return OK
