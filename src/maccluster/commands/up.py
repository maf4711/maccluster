"""up command."""

from __future__ import annotations

from maccluster.app_factory import AppContext
from maccluster.cli.exit_codes import OK
from maccluster.errors import DegradedError
from maccluster.render.json_out import dumps, to_jsonable
from maccluster.services.mutate_service import ensure_local


def run(ctx: AppContext, args) -> int:
    dry_run = bool(getattr(args, "dry_run", False))
    try:
        result = ensure_local(ctx, dry_run=dry_run)
    except DegradedError as exc:
        if ctx.json_mode:
            print(
                dumps(
                    "up",
                    {"degraded": True, "message": exc.message, "details": to_jsonable(exc.details)},
                )
            )
        else:
            print(exc.message)
        return exc.exit_code
    if ctx.json_mode:
        print(dumps("up", to_jsonable(result)))
    else:
        print(result.message)
        print(f"interface={result.interface} ip={result.ip} tb_links={result.tb_links}")
    return OK
