"""Shared Home + ~/Developer transfer for ``pull`` / ``push`` shortcuts."""

from __future__ import annotations

import sys
from types import SimpleNamespace

from maccluster.app_factory import AppContext
from maccluster.commands import sync_cmd
from maccluster.constants import SYNC_PULL_DEFAULT_PRESETS


def resolve_presets(args) -> tuple[str, ...]:
    user_presets = tuple(getattr(args, "preset", None) or ())
    if bool(getattr(args, "full_home", False)):
        return ()
    if user_presets:
        return user_presets
    return SYNC_PULL_DEFAULT_PRESETS


def run_transfer(
    ctx: AppContext,
    args,
    *,
    command: str,
    default_push_only: bool = False,
    default_pull_only: bool = False,
) -> int:
    """Map shortcut flags onto ``sync home`` and run it.

    Direction defaults apply only when the user did not set ``--push-only`` /
    ``--pull-only`` / ``--both`` / ``--identical``.
    """
    presets = resolve_presets(args)

    explicit_push = bool(getattr(args, "push_only", False))
    explicit_pull = bool(getattr(args, "pull_only", False))
    both = bool(getattr(args, "both", False))
    identical = bool(getattr(args, "identical", False))

    if both or identical:
        push_only = False
        pull_only = False
    elif explicit_push or explicit_pull:
        push_only = explicit_push
        pull_only = explicit_pull
    else:
        push_only = default_push_only
        pull_only = default_pull_only

    sync_args = SimpleNamespace(
        sync_action="home",
        last=False,
        dry_run=bool(getattr(args, "dry_run", False)),
        compare=bool(getattr(args, "compare", False)),
        peer=getattr(args, "peer", None),
        push_only=push_only,
        pull_only=pull_only,
        user=getattr(args, "user", None),
        home=getattr(args, "home", None),
        remote_home=getattr(args, "remote_home", None),
        exclude=list(getattr(args, "exclude", None) or []),
        exclude_from=getattr(args, "exclude_from", None),
        preset=list(presets),
        include=list(getattr(args, "include", None) or []),
        conflict_policy=getattr(args, "conflict_policy", None) or "newer",
        safetynet=bool(getattr(args, "safetynet", False)),
        verify=bool(getattr(args, "verify", False)),
        verify_sample=int(getattr(args, "verify_sample", 20) or 20),
        quick=bool(getattr(args, "quick", False)),
        max_files=getattr(args, "max_files", None),
        max_bytes=getattr(args, "max_bytes", None),
        min_free=getattr(args, "min_free", None),
        apfs_snapshot=bool(getattr(args, "apfs_snapshot", False)),
        notify=bool(getattr(args, "notify", False)),
        no_speedtest=bool(getattr(args, "no_speedtest", False)),
        timeout=getattr(args, "timeout", None),
        no_progress=bool(getattr(args, "no_progress", False)),
        force_icloud=bool(getattr(args, "force_icloud", False)),
        identical=identical,
        icloud_timeout=float(getattr(args, "icloud_timeout", 20.0) or 20.0),
        icloud_max_seconds=float(getattr(args, "icloud_max_seconds", 900.0) or 900.0),
    )

    if not ctx.json_mode:
        scope = "full $HOME" if not presets else f"presets={','.join(presets)}"
        if push_only and not pull_only:
            direction = "push-only"
        elif pull_only and not push_only:
            direction = "pull-only"
        else:
            direction = "two-way"
        print(
            f"maccluster {command} → sync home ({direction}, {scope})",
            file=sys.stderr,
        )

    return int(sync_cmd.run(ctx, sync_args))
