"""keychain — store/load MacCluster config in macOS Keychain."""

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
            print("pushed local config → Keychain")
            print(format_keychain_snapshot(snap))
            print("Peers with same Apple ID + iCloud Keychain: maccluster init / keychain pull")
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

    raise CliError(
        "keychain requires show|push|pull|delete",
        exit_code=2,
    )
