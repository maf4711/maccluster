"""status command."""

from __future__ import annotations

from maccluster.app_factory import AppContext
from maccluster.render.json_out import dumps, to_jsonable
from maccluster.render.plain import render_status
from maccluster.services.status_service import collect_status


def run(ctx: AppContext, args) -> int:
    include_exo = bool(getattr(args, "exo", False))
    exo_url = getattr(args, "exo_url", None)
    snap, code = collect_status(
        ctx,
        include_exo=include_exo,
        exo_base_url=exo_url,
    )
    if ctx.json_mode:
        print(dumps("status", to_jsonable(snap)))
    else:
        print(render_status(snap))
    return code
