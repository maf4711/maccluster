"""config show | validate."""

from __future__ import annotations

from maccluster.app_factory import AppContext
from maccluster.cli.exit_codes import OK
from maccluster.render.json_out import dumps, to_jsonable
from maccluster.render.plain import render_config
from maccluster.services.config_service import load_and_bind_self, load_config, validate_only


def run(ctx: AppContext, args) -> int:
    action = getattr(args, "config_action", None) or getattr(args, "action", "show")
    if action == "refresh-tb":
        from maccluster.commands.config_refresh_tb import run as run_refresh_tb

        return run_refresh_tb(ctx, args)
    if action == "validate":
        cfg, self_node = validate_only(ctx)
        if ctx.json_mode:
            print(
                dumps(
                    "config.validate",
                    {"ok": True, "self": self_node.id, "config": to_jsonable(cfg)},
                )
            )
        else:
            print(f"config ok: {ctx.config_path}")
            print(f"self: {self_node.id} ({self_node.ip})")
            print(render_config(cfg, self_id=self_node.id))
        return OK

    # show
    try:
        cfg, self_node = load_and_bind_self(ctx)
        self_id = self_node.id
    except Exception:
        cfg = load_config(ctx)
        self_id = None
    if ctx.json_mode:
        print(dumps("config.show", {"path": str(ctx.config_path), "config": to_jsonable(cfg)}))
    else:
        print(f"path: {ctx.config_path}")
        print(render_config(cfg, self_id=self_id))
    return OK
