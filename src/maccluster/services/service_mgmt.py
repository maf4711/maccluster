"""LaunchAgent service management."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

from maccluster.app_factory import AppContext
from maccluster.constants import (
    DEFAULT_SYNC_INTERVAL_S,
    DEFAULT_WATCHDOG_INTERVAL_S,
    LAUNCH_AGENT_LABEL,
    LAUNCH_AGENT_SYNC_LABEL,
    LAUNCH_AGENT_WATCHDOG_LABEL,
    MIN_SYNC_INTERVAL_S,
)
from maccluster.domain.models import ServiceState
from maccluster.errors import CliError
from maccluster.services.config_service import load_config


def resolve_program() -> Path:
    # Prefer installed entry point on PATH
    which = shutil.which("maccluster")
    if which:
        return Path(which).resolve()
    # Fallback: python -m maccluster via current interpreter wrapper is not ideal for launchd;
    # use sys.executable with -m if needed
    return Path(sys.executable).resolve()


def _resolve_maccluster_program() -> Path:
    program = resolve_program()
    if program.name.startswith("python"):
        candidate = Path(sys.prefix) / "bin" / "maccluster"
        if candidate.is_file():
            return candidate
        raise CliError(
            "maccluster entry point not found on PATH; install package first "
            "(pipx install . or pip install -e .)",
            exit_code=1,
        )
    return program


def install_service(ctx: AppContext) -> ServiceState:
    try:
        cfg = load_config(ctx)
        interval = cfg.heal_interval_seconds
    except Exception:
        interval = 30

    program = _resolve_maccluster_program()
    state = ctx.service.install(
        program=program,
        config_path=ctx.config_path,
        interval_seconds=interval,
        label=LAUNCH_AGENT_LABEL,
    )
    # Keepalive watchdog: kickstart heal if heartbeat goes stale (silent hang)
    try:
        wd = ctx.service.install(
            program=program,
            config_path=ctx.config_path,
            interval_seconds=DEFAULT_WATCHDOG_INTERVAL_S,
            label=LAUNCH_AGENT_WATCHDOG_LABEL,
        )
        detail = f"{state.detail}; watchdog={wd.detail}"
    except Exception as exc:
        detail = f"{state.detail}; watchdog_install_failed={exc}"
    return ServiceState(
        label=state.label,
        installed=state.installed,
        running=state.running,
        plist_path=state.plist_path,
        interval_seconds=state.interval_seconds,
        detail=detail,
    )


def uninstall_service(ctx: AppContext) -> ServiceState:
    try:
        ctx.service.uninstall(label=LAUNCH_AGENT_WATCHDOG_LABEL)
    except Exception:
        pass
    return ctx.service.uninstall(label=LAUNCH_AGENT_LABEL)


def service_status(ctx: AppContext) -> ServiceState:
    state = ctx.service.status(label=LAUNCH_AGENT_LABEL)
    try:
        wd = ctx.service.status(label=LAUNCH_AGENT_WATCHDOG_LABEL)
        wd_bit = f"watchdog installed={wd.installed} running={wd.running}"
    except Exception:
        wd_bit = "watchdog n/a"
    try:
        from maccluster.services.heal_heartbeat import read_heartbeat

        try:
            cfg = load_config(ctx)
            interval = float(cfg.heal_interval_seconds)
        except Exception:
            interval = 30.0
        hb = read_heartbeat(interval_seconds=interval)
        hb_bit = hb.detail
    except Exception:
        hb_bit = "heartbeat n/a"
    detail = f"{state.detail}; {wd_bit}; {hb_bit}"
    return ServiceState(
        label=state.label,
        installed=state.installed,
        running=state.running,
        plist_path=state.plist_path,
        interval_seconds=state.interval_seconds,
        detail=detail,
    )


def install_sync_service(ctx: AppContext, *, interval_seconds: int | None = None) -> ServiceState:
    """Install LaunchAgent for periodic ``sync home`` (CCC schedule analogue)."""
    interval = int(interval_seconds or DEFAULT_SYNC_INTERVAL_S)
    if interval < MIN_SYNC_INTERVAL_S:
        raise CliError(
            f"sync interval must be >= {MIN_SYNC_INTERVAL_S} seconds",
            exit_code=2,
        )
    program = _resolve_maccluster_program()
    return ctx.service.install(
        program=program,
        config_path=ctx.config_path,
        interval_seconds=interval,
        label=LAUNCH_AGENT_SYNC_LABEL,
    )


def uninstall_sync_service(ctx: AppContext) -> ServiceState:
    return ctx.service.uninstall(label=LAUNCH_AGENT_SYNC_LABEL)


def sync_service_status(ctx: AppContext) -> ServiceState:
    return ctx.service.status(label=LAUNCH_AGENT_SYNC_LABEL)
