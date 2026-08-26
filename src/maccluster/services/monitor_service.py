"""Live monitor loop."""

from __future__ import annotations

import sys

from maccluster.app_factory import AppContext
from maccluster.constants import DEFAULT_MONITOR_INTERVAL_S
from maccluster.health.traffic import TrafficSampler
from maccluster.render.plain import render_status
from maccluster.render.rich_monitor import render_rich_status, rich_available
from maccluster.services.status_service import collect_status, get_traffic_sampler


def run_monitor(
    ctx: AppContext,
    *,
    interval: float = DEFAULT_MONITOR_INTERVAL_S,
    max_iterations: int | None = None,
    out=None,
) -> int:
    """Refresh status until Ctrl+C. Returns 0 on clean exit.

    Live TX/RX rates come from netstat counter deltas between refresh ticks
    (and disk cache for the first tick after a prior status/monitor run).
    """
    out = out or sys.stdout
    n = 0
    use_rich = rich_available() and not ctx.no_color and not ctx.json_mode
    # Shared sampler so rates use previous monitor tick (and disk cache).
    sampler: TrafficSampler = get_traffic_sampler()
    try:
        while True:
            snap, _code = collect_status(ctx, sampler=sampler, persist_traffic=True)
            if ctx.json_mode:
                from maccluster.render.json_out import dumps, to_jsonable

                text = dumps("monitor", to_jsonable(snap))
            elif use_rich:
                try:
                    text = render_rich_status(snap)
                except Exception:
                    text = render_status(snap)
            else:
                text = render_status(snap)
            # Clear-ish: print separator for plain mode
            out.write(text + "\n")
            out.write("---\n")
            out.flush()
            n += 1
            if max_iterations is not None and n >= max_iterations:
                return 0
            ctx.clock.sleep(interval)
    except KeyboardInterrupt:
        return 0
