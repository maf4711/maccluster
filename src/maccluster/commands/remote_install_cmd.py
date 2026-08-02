"""remote-install command — peer install over TB bridge only."""

from __future__ import annotations

from maccluster.app_factory import AppContext
from maccluster.errors import CliError
from maccluster.render.json_out import dumps, to_jsonable
from maccluster.services.remote_install_service import remote_install


def run(ctx: AppContext, args) -> int:
    peer = getattr(args, "peer", None) or getattr(args, "target", None)
    if not peer:
        raise CliError(
            "remote-install requires peer id or cluster IP "
            "(e.g. maccluster remote-install node-b)",
            exit_code=2,
        )
    result = remote_install(
        ctx,
        peer,
        user=getattr(args, "user", None),
        copy_config=not bool(getattr(args, "no_config", False)),
        dry_run=bool(getattr(args, "dry_run", False)),
        setup_ssh_config=not bool(getattr(args, "no_ssh_config", False)),
        timeout=float(getattr(args, "timeout", None) or 600.0),
    )
    if ctx.json_mode:
        print(dumps("remote-install", to_jsonable(result)))
    else:
        status = "OK" if result.ok else "FAIL"
        print(f"remote-install [{status}]")
        print(f"  peer={result.peer_id} ({result.peer_ip})")
        print(f"  bind={result.bind_ip}  (TB bridge only)")
        print(f"  ssh={result.ssh_target}")
        print(f"  wheel={result.wheel}")
        print(f"  {result.message}")
        if result.log and (ctx.verbose or not result.ok):
            print("--- remote log ---")
            print(result.log)
    return 0 if result.ok else 1
