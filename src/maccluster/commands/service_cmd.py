"""service install|uninstall|status."""

from __future__ import annotations

from maccluster.app_factory import AppContext
from maccluster.cli.exit_codes import OK
from maccluster.render.json_out import dumps, to_jsonable
from maccluster.render.plain import render_service
from maccluster.services import service_mgmt


def run(ctx: AppContext, args) -> int:
    action = getattr(args, "service_action", None) or getattr(args, "action", "status")
    if action == "install":
        state = service_mgmt.install_service(ctx)
    elif action == "uninstall":
        state = service_mgmt.uninstall_service(ctx)
    else:
        state = service_mgmt.service_status(ctx)

    if ctx.json_mode:
        print(dumps("service", to_jsonable(state)))
    else:
        print(render_service(state))
    return OK
