"""monitor command."""

from __future__ import annotations

from maccluster.app_factory import AppContext
from maccluster.constants import DEFAULT_MONITOR_INTERVAL_S
from maccluster.services.monitor_service import run_monitor


def run(ctx: AppContext, args) -> int:
    interval = float(
        getattr(args, "interval", DEFAULT_MONITOR_INTERVAL_S) or DEFAULT_MONITOR_INTERVAL_S
    )
    return run_monitor(ctx, interval=interval)
