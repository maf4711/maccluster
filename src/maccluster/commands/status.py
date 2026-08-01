"""status command."""

from __future__ import annotations

from maccluster.app_factory import AppContext
from maccluster.render.json_out import dumps, to_jsonable
from maccluster.render.plain import render_status
from maccluster.services.status_service import collect_status


def run(ctx: AppContext, args) -> int:
    snap, code = collect_status(ctx)
    if ctx.json_mode:
        print(dumps("status", to_jsonable(snap)))
    else:
        print(render_status(snap))
    return code
