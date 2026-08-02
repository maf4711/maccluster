"""init command."""

from __future__ import annotations

from maccluster.app_factory import AppContext
from maccluster.cli.exit_codes import OK
from maccluster.render.json_out import dumps
from maccluster.services.init_service import apply_keychain_ssh_targets, init_cluster
from maccluster.services.keychain_service import show_keychain


def run(ctx: AppContext, args) -> int:
    # Always inspect Keychain first (peer init: "what's already shared?")
    snap = show_keychain(ctx)
    if not ctx.json_mode:
        print("keychain check:")
        print(
            f"  config={'yes' if snap.has_config else 'no'}"
            f"  ssh_user={snap.ssh_user or '-'}"
            f"  password={'set' if snap.has_ssh_password else '-'}"
        )
        if snap.has_config:
            print(
                f"  found cluster={snap.cluster_name!r} — will prefer Keychain unless --no-keychain"
            )

    path, source = init_cluster(
        ctx,
        force=bool(getattr(args, "force", False)),
        name=getattr(args, "name", None) or "studio-cluster",
        node_count=int(getattr(args, "nodes", 4) or 4),
        from_keychain=not bool(getattr(args, "no_keychain", False)),
        save_keychain=not bool(getattr(args, "no_keychain", False)),
    )
    apply_keychain_ssh_targets(ctx)
    if ctx.json_mode:
        print(dumps("init", {"path": str(path), "source": source}))
    else:
        print(f"wrote {path}  (source={source})")
        if source == "keychain":
            print("restored from macOS Keychain (iCloud Keychain shares this with peer if enabled)")
        else:
            print("template written; saved to Keychain for peer pull (maccluster keychain pull)")
    return OK
