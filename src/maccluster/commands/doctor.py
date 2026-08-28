"""doctor command."""

from __future__ import annotations

from maccluster.app_factory import AppContext
from maccluster.cli.exit_codes import USAGE
from maccluster.errors import CliError
from maccluster.render.json_out import dumps, to_jsonable
from maccluster.render.plain import render_doctor
from maccluster.services.doctor_service import run_doctor


def run(ctx: AppContext, args) -> int:
    include_exo = bool(getattr(args, "exo", False))
    exo_url = getattr(args, "exo_url", None)
    include_host = bool(getattr(args, "host", False))
    include_fleet = bool(getattr(args, "fleet", False))
    peer = getattr(args, "peer", None)
    if include_fleet and not include_host:
        raise CliError("doctor --fleet requires --host", exit_code=USAGE)
    if peer and not include_fleet:
        raise CliError("doctor --peer requires --host --fleet", exit_code=USAGE)
    report = run_doctor(
        ctx,
        include_exo=include_exo,
        exo_base_url=exo_url,
        include_host=include_host,
        include_fleet=include_fleet,
        peer=peer,
    )
    if ctx.json_mode:
        print(dumps("doctor", to_jsonable(report)))
    else:
        print(render_doctor(report))
    return report.exit_code
