"""LaunchAgent service management."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

from maccluster.app_factory import AppContext
from maccluster.constants import LAUNCH_AGENT_LABEL
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


def install_service(ctx: AppContext) -> ServiceState:
    try:
        cfg = load_config(ctx)
        interval = cfg.heal_interval_seconds
    except Exception:
        interval = 30

    program = resolve_program()
    # If we only have python, ProgramArguments should still work if we point to a wrapper.
    # Prefer maccluster on PATH; if missing, create argv via python -m is handled by writing
    # program as sys.executable and args — LaunchAgent template expects maccluster binary.
    if program.name.startswith("python"):
        # Look for console script in same environment
        candidate = Path(sys.prefix) / "bin" / "maccluster"
        if candidate.is_file():
            program = candidate
        else:
            raise CliError(
                "maccluster entry point not found on PATH; install package first "
                "(pipx install . or pip install -e .)",
                exit_code=1,
            )

    return ctx.service.install(
        program=program,
        config_path=ctx.config_path,
        interval_seconds=interval,
        label=LAUNCH_AGENT_LABEL,
    )


def uninstall_service(ctx: AppContext) -> ServiceState:
    return ctx.service.uninstall(label=LAUNCH_AGENT_LABEL)


def service_status(ctx: AppContext) -> ServiceState:
    return ctx.service.status(label=LAUNCH_AGENT_LABEL)
