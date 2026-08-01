"""tb command."""

from __future__ import annotations

from maccluster.app_factory import AppContext
from maccluster.cli.exit_codes import OK
from maccluster.render.json_out import dumps, to_jsonable
from maccluster.render.plain import render_tb
from maccluster.services.tb_service import probe_tb


def run(ctx: AppContext, args) -> int:
    snap = probe_tb(ctx)
    if ctx.json_mode:
        print(dumps("tb", to_jsonable(snap)))
    else:
        print(render_tb(snap))
    return OK
