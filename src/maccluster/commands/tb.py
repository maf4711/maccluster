"""tb command."""

from __future__ import annotations

from maccluster.adapters.rdma_ctl import probe_rdma
from maccluster.app_factory import AppContext
from maccluster.cli.exit_codes import OK
from maccluster.render.json_out import dumps, to_jsonable
from maccluster.render.plain import render_tb
from maccluster.services.tb_service import probe_tb


def run(ctx: AppContext, args) -> int:
    snap = probe_tb(ctx)
    try:
        rdma = probe_rdma(ctx.runner)
    except Exception:
        rdma = None
    if ctx.json_mode:
        payload = to_jsonable(snap)
        if isinstance(payload, dict):
            payload["rdma"] = to_jsonable(rdma)
        print(
            dumps(
                "tb",
                payload
                if isinstance(payload, dict)
                else {"tb": payload, "rdma": to_jsonable(rdma)},
            )
        )
    else:
        print(render_tb(snap, rdma=rdma))
    return OK
