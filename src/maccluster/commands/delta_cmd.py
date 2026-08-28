"""delta command — inventory → precise compare → optional difference sync.

Not a bulk mirror: reads inventories on self + peers (or N of them), computes
exact file deltas (mtime/size policy), reports byte-accurate buckets, then
optionally transfers only the planned difference via Apple ditto.
"""

from __future__ import annotations

import sys
from types import SimpleNamespace

from maccluster.app_factory import AppContext
from maccluster.commands import sync_cmd
from maccluster.commands.home_dev_transfer import resolve_presets
from maccluster.errors import CliError


def run(ctx: AppContext, args) -> int:
    apply = bool(getattr(args, "apply", False))
    dry_run = bool(getattr(args, "dry_run", False))
    # Default: report only (inventory + precise delta). --apply transfers.
    # --dry-run keeps plan-only even with --apply (stage plan without writes).
    compare_only = not apply
    if apply and dry_run:
        compare_only = False  # dry-run path inside sync_home (no archives)

    presets = resolve_presets(args)
    peer_limit = getattr(args, "limit", None)
    if peer_limit is not None and int(peer_limit) < 1:
        raise CliError("--limit must be >= 1", exit_code=2)

    explicit_push = bool(getattr(args, "push_only", False))
    explicit_pull = bool(getattr(args, "pull_only", False))
    if explicit_push and explicit_pull:
        raise CliError("use only one of --push-only / --pull-only", exit_code=2)

    sync_args = SimpleNamespace(
        sync_action="home",
        last=False,
        dry_run=dry_run and apply,  # report mode uses compare_only; apply+dry uses dry_run
        compare=compare_only,
        peer=getattr(args, "peer", None),
        peer_limit=peer_limit,
        push_only=explicit_push,
        pull_only=explicit_pull,
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
        identical=bool(getattr(args, "identical", False)),
        icloud_timeout=float(getattr(args, "icloud_timeout", 20.0) or 20.0),
        icloud_max_seconds=float(getattr(args, "icloud_max_seconds", 900.0) or 900.0),
    )

    if not ctx.json_mode:
        scope = "full $HOME" if not presets else f"presets={','.join(presets)}"
        phase = "apply" if apply else "report"
        if apply and dry_run:
            phase = "apply-dry-run"
        lim = f" limit={peer_limit}" if peer_limit else ""
        peer = getattr(args, "peer", None)
        peer_s = f" peer={peer}" if peer else " peers=inventory"
        print(
            f"maccluster delta → inventory · compare · {phase} ({scope}{peer_s}{lim})",
            file=sys.stderr,
        )
        if not apply:
            print(
                "  (difference report only — pass --apply to transfer deltas)",
                file=sys.stderr,
            )

    return int(sync_cmd.run(ctx, sync_args))
