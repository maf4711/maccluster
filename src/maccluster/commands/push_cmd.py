"""push command — shortcut: push Home + Developer to peers (local → peer).

``maccluster push`` runs the CCC-style Home sync layer scoped to practical
home folders plus ``~/Developer`` (not full ``$HOME``; use ``--full-home``).

Default is **push-only** (local → peer). Use ``--both`` for two-way, or
``--pull-only`` to reverse.
"""

from __future__ import annotations

from maccluster.app_factory import AppContext
from maccluster.commands.home_dev_transfer import run_transfer


def run(ctx: AppContext, args) -> int:
    return run_transfer(
        ctx,
        args,
        command="push",
        default_push_only=True,
        default_pull_only=False,
    )
