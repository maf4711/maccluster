"""service install|uninstall|status (+ sync-install schedule)."""

from __future__ import annotations

from maccluster.app_factory import AppContext
from maccluster.cli.exit_codes import OK, USAGE
from maccluster.render.json_out import dumps, to_jsonable
from maccluster.render.plain import render_service
from maccluster.services import service_mgmt


def run(ctx: AppContext, args) -> int:
    action = getattr(args, "service_action", None) or getattr(args, "action", "status")
    if action == "install":
        state = service_mgmt.install_service(ctx)
    elif action == "uninstall":
        state = service_mgmt.uninstall_service(ctx)
    elif action == "sync-install":
        interval = getattr(args, "interval", None)
        state = service_mgmt.install_sync_service(ctx, interval_seconds=interval)
    elif action == "sync-uninstall":
        state = service_mgmt.uninstall_sync_service(ctx)
    elif action == "sync-status":
        state = service_mgmt.sync_service_status(ctx)
    elif action == "status":
        state = service_mgmt.service_status(ctx)
    else:
        print(
            "error: service requires install|uninstall|status|"
            "sync-install|sync-uninstall|sync-status",
            file=__import__("sys").stderr,
        )
        return USAGE

    if ctx.json_mode:
        print(dumps("service", to_jsonable(state)))
    else:
        print(render_service(state))
    return OK
