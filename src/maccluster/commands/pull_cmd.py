"""pull command — shortcut: sync Home + Developer with peers (two-way default)."""

from __future__ import annotations

from maccluster.app_factory import AppContext
from maccluster.commands.home_dev_transfer import run_transfer


def run(ctx: AppContext, args) -> int:
    return run_transfer(
        ctx,
        args,
        command="pull",
        default_push_only=False,
        default_pull_only=False,
    )
