"""init command."""

from __future__ import annotations

from maccluster.app_factory import AppContext
from maccluster.cli.exit_codes import OK
from maccluster.render.json_out import dumps
from maccluster.services.init_service import init_cluster


def run(ctx: AppContext, args) -> int:
    path = init_cluster(
        ctx,
        force=bool(getattr(args, "force", False)),
        name=getattr(args, "name", None) or "studio-cluster",
        node_count=int(getattr(args, "nodes", 4) or 4),
    )
    if ctx.json_mode:
        print(dumps("init", {"path": str(path)}))
    else:
        print(f"wrote {path}")
    return OK
