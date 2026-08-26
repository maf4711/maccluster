"""Periodic heal loop (best-effort, not HA) + watchdog kick."""

from __future__ import annotations

import os

from maccluster.app_factory import AppContext
from maccluster.constants import (
    LAUNCH_AGENT_LABEL,
    MIN_HEAL_INTERVAL_S,
    TIMEOUT_GENERIC,
)
from maccluster.errors import CliError, DegradedError, PrivilegeError
from maccluster.services.config_service import load_config
from maccluster.services.heal_heartbeat import read_heartbeat, write_heartbeat
from maccluster.services.mutate_service import ensure_local


def run_heal_loop(
    ctx: AppContext, *, interval: float | None = None, max_iterations: int | None = None
) -> int:
    """Run heal repeatedly until KeyboardInterrupt.

    Returns exit code of last iteration (0 default). max_iterations for tests.
    Writes a heartbeat each tick so the watchdog can detect hung processes.
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

        try:
            write_heartbeat(
                ok=(last_code == 0),
                exit_code=last_code,
                interval_seconds=delay,
            )
        except Exception:
            pass

        n += 1
        if max_iterations is not None and n >= max_iterations:
            return last_code
        try:
            ctx.clock.sleep(delay)
        except KeyboardInterrupt:
            return 0


def run_heal_watchdog(ctx: AppContext) -> int:
    """One-shot: if heal heartbeat is stale, kickstart the heal LaunchAgent.

    Analogous to exo-keepalive: KeepAlive alone does not detect silent hangs.
    """
    try:
        cfg = load_config(ctx)
        interval = float(cfg.heal_interval_seconds)
    except Exception:
        interval = 30.0

    try:
        st = ctx.service.status(label=LAUNCH_AGENT_LABEL)
    except Exception:
        st = None

    if st is None or not st.installed:
        if ctx.verbose:
            print("heal-watchdog: heal service not installed — noop", flush=True)
        return 0

    hb = read_heartbeat(interval_seconds=interval)
    if not hb.stale:
        if ctx.verbose:
            print(f"heal-watchdog: ok ({hb.detail})", flush=True)
        return 0

    # Kick heal agent via launchctl
    uid = os.getuid()
    domain_label = f"gui/{uid}/{LAUNCH_AGENT_LABEL}"
    try:
        r = ctx.runner.run(
            ["launchctl", "kickstart", "-k", domain_label],
            timeout=TIMEOUT_GENERIC,
        )
        msg = (
            f"heal-watchdog: heartbeat stale ({hb.detail}); "
            f"kickstart {domain_label} rc={r.returncode}"
        )
        print(msg, flush=True)
        return 0 if r.returncode == 0 else 1
    except Exception as exc:
        print(f"heal-watchdog: kick failed: {exc}", flush=True)
        return 1
