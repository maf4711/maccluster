"""Periodic heal loop (best-effort, not HA)."""

from __future__ import annotations

from maccluster.app_factory import AppContext
from maccluster.constants import MIN_HEAL_INTERVAL_S
from maccluster.errors import CliError, DegradedError, PrivilegeError
from maccluster.services.config_service import load_config
from maccluster.services.mutate_service import ensure_local


def run_heal_loop(
    ctx: AppContext, *, interval: float | None = None, max_iterations: int | None = None
) -> int:
    """Run heal repeatedly until KeyboardInterrupt.

    Returns exit code of last iteration (0 default). max_iterations for tests.
    """
    try:
        cfg = load_config(ctx)
        default_interval = float(cfg.heal_interval_seconds)
    except Exception:
        default_interval = 30.0

    delay = float(interval if interval is not None else default_interval)
    delay = max(float(MIN_HEAL_INTERVAL_S), delay)

    last_code = 0
    n = 0
    while True:
        try:
            ensure_local(ctx)
            last_code = 0
        except DegradedError:
            last_code = 3
        except PrivilegeError as exc:
            # Keep looping but report; LaunchAgent will restart process if crash
            last_code = 1
            if ctx.verbose:
                print(f"heal: {exc}", flush=True)
        except CliError as exc:
            last_code = exc.exit_code
            if ctx.verbose:
                print(f"heal: {exc}", flush=True)
        except Exception as exc:
            last_code = 1
            if ctx.verbose:
                print(f"heal: unexpected {exc}", flush=True)

        n += 1
        if max_iterations is not None and n >= max_iterations:
            return last_code
        try:
            ctx.clock.sleep(delay)
        except KeyboardInterrupt:
            return 0
