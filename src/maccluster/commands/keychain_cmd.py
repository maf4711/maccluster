"""keychain — store/load MacCluster config in macOS Keychain (local + push-peer)."""

from __future__ import annotations

from maccluster.app_factory import AppContext
from maccluster.cli.exit_codes import OK
from maccluster.constants import KEYCHAIN_ACCOUNT_DEFAULT
from maccluster.errors import CliError
from maccluster.render.json_out import dumps, to_jsonable
from maccluster.services.init_service import apply_keychain_ssh_targets
from maccluster.services.keychain_service import (
    delete_keychain,
    format_keychain_snapshot,
    pull_config_from_keychain,
    push_config_to_keychain,
    push_config_to_peer,
    show_keychain,
)


def run(ctx: AppContext, args) -> int:
    action = getattr(args, "keychain_action", None) or "show"
    account = getattr(args, "account", None) or KEYCHAIN_ACCOUNT_DEFAULT

    if action == "show":
        snap = show_keychain(ctx, account=account)
        if ctx.json_mode:
            print(dumps("keychain", to_jsonable(snap)))
        else:
            print(format_keychain_snapshot(snap))
        return OK

    if action == "push":
        snap = push_config_to_keychain(
            ctx,
            account=account,
            ssh_user=getattr(args, "ssh_user", None),
            ssh_password=getattr(args, "ssh_password", None),
        )
        apply_keychain_ssh_targets(ctx, account=account)
        if ctx.json_mode:
            print(dumps("keychain", to_jsonable(snap)))
        else:
            print("pushed local config → this Mac's Keychain (local only, not iCloud)")
            print(format_keychain_snapshot(snap))
            print(
                "Peer access: maccluster keychain push-peer <peer>  "
                "(or remote-install). Keychain items do not sync via Apple ID."
            )
        return OK

    if action == "pull":
        path, snap = pull_config_from_keychain(
            ctx,
            account=account,
            force=bool(getattr(args, "force", False)),
        )
        apply_keychain_ssh_targets(ctx, account=account)
        if ctx.json_mode:
            print(dumps("keychain", {"path": str(path), **to_jsonable(snap)}))
        else:
            print(f"pulled Keychain → {path}")
            print(format_keychain_snapshot(snap))
        return OK

    if action == "delete":
        removed = delete_keychain(ctx, account=account)
        if ctx.json_mode:
            print(dumps("keychain", {"deleted": removed}))
        else:
            print("deleted Keychain items: " + (", ".join(removed) if removed else "(none)"))
        return OK

    if action == "push-peer":
        peer = getattr(args, "peer", None)
        if not peer:
            raise CliError(
                "keychain push-peer requires peer id or cluster IP "
                "(e.g. maccluster keychain push-peer node-b)",
                exit_code=2,
            )
        result = push_config_to_peer(
            ctx,
            peer,
            account=account,
            user=getattr(args, "user", None),
            force=bool(getattr(args, "force", False)),
            plant_keychain=not bool(getattr(args, "no_keychain", False)),
        )
        if ctx.json_mode:
            print(dumps("keychain-push-peer", to_jsonable(result)))
        else:
            status = "ok" if result.config_planted else "fail"
            print(f"keychain push-peer [{status}]")
            print(f"  peer={result.peer_id} ({result.peer_ip}) via {result.ssh_target}")
            print(f"  bind={result.bind_ip}")
            print(f"  config_planted={result.config_planted}")
            print(f"  keychain_pushed={result.keychain_pushed}")
            print(f"  {result.message}")
            if result.log and getattr(args, "verbose", False):
                print("--- remote log ---")
                print(result.log)
        # Config plant is success even if peer Keychain stayed locked
        return OK if result.config_planted else 1

    raise CliError(
        "keychain requires show|push|pull|delete|push-peer",
        exit_code=2,
    )
