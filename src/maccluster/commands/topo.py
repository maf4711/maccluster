"""topo command."""

from __future__ import annotations

from maccluster.app_factory import AppContext
from maccluster.cli.exit_codes import OK
from maccluster.render.json_out import dumps, to_jsonable
from maccluster.render.plain import render_topo
from maccluster.services.topo_service import collect_topology


def run(ctx: AppContext, args) -> int:
    topo = collect_topology(ctx)
    if ctx.json_mode:
        print(dumps("topo", to_jsonable(topo)))
    else:
        print(render_topo(topo))
    return OK
