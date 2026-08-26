"""doctor command."""

from __future__ import annotations

from maccluster.app_factory import AppContext
from maccluster.render.json_out import dumps, to_jsonable
from maccluster.render.plain import render_doctor
from maccluster.services.doctor_service import run_doctor


def run(ctx: AppContext, args) -> int:
    include_exo = bool(getattr(args, "exo", False))
    exo_url = getattr(args, "exo_url", None)
    report = run_doctor(ctx, include_exo=include_exo, exo_base_url=exo_url)
    if ctx.json_mode:
        print(dumps("doctor", to_jsonable(report)))
    else:
        print(render_doctor(report))
    return report.exit_code
