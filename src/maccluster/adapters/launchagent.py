"""User-domain LaunchAgent install/uninstall/status."""

from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path

from maccluster.adapters.plist_template import (
    render_automation_plist,
    render_heal_plist,
    render_sync_plist,
    render_watchdog_plist,
)
from maccluster.constants import (
    DEFAULT_WATCHDOG_INTERVAL_S,
    LAUNCH_AGENT_LABEL,
    LAUNCH_AGENT_PLIST,
    LAUNCH_AGENT_SYNC_LABEL,
    LAUNCH_AGENT_SYNC_PLIST,
    LAUNCH_AGENT_WATCHDOG_LABEL,
    LAUNCH_AGENT_WATCHDOG_PLIST,
    TIMEOUT_GENERIC,
)
from maccluster.domain.models import ServiceState
from maccluster.errors import CliError
from maccluster.ports.process import ProcessRunnerPort

AUTOMATION_LABEL = "com.maccluster.automation"


def launch_agents_dir() -> Path:
    home = Path.home()
    return home / "Library" / "LaunchAgents"


class LaunchAgentService:
    def __init__(self, runner: ProcessRunnerPort, fs_write=None) -> None:
        self._runner = runner
        self._fs_write = fs_write

    def _plist_path(self, label: str = LAUNCH_AGENT_LABEL) -> Path:
        name = f"{label}.plist" if not label.endswith(".plist") else label
        if label == LAUNCH_AGENT_LABEL:
            name = LAUNCH_AGENT_PLIST
        elif label == LAUNCH_AGENT_SYNC_LABEL:
            name = LAUNCH_AGENT_SYNC_PLIST
        elif label == LAUNCH_AGENT_WATCHDOG_LABEL:
            name = LAUNCH_AGENT_WATCHDOG_PLIST
        return launch_agents_dir() / name

    def install(
        self,
        *,
        program: Path,
        config_path: Path,
        interval_seconds: int,
        label: str = LAUNCH_AGENT_LABEL,
    ) -> ServiceState:
        program = program.resolve()
        config_path = config_path.expanduser().resolve()
        if not program.is_file():
            raise CliError(f"maccluster program not found: {program}", exit_code=1)
        plist_path = self._plist_path(label)
        plist_path.parent.mkdir(parents=True, exist_ok=True)
        if plist_path.is_symlink():
            raise CliError(f"refusing to write through symlink: {plist_path}", exit_code=2)
        if label == AUTOMATION_LABEL:
            if interval_seconds < 300:
                raise CliError("automation interval must be >= 300 seconds", exit_code=2)
            (Path.home() / "Library" / "Logs" / "maccluster").mkdir(parents=True, exist_ok=True)
            content = render_automation_plist(
                label=label,
                program=str(program),
                config_path=str(config_path),
                interval_seconds=interval_seconds,
            )
        elif label == LAUNCH_AGENT_SYNC_LABEL:
            content = render_sync_plist(
                label=label,
                program=str(program),
                config_path=str(config_path),
                interval_seconds=max(300, interval_seconds),
            )
        elif label == LAUNCH_AGENT_WATCHDOG_LABEL:
            content = render_watchdog_plist(
                label=label,
                program=str(program),
                config_path=str(config_path),
                interval_seconds=max(
                    DEFAULT_WATCHDOG_INTERVAL_S,
                    int(interval_seconds or DEFAULT_WATCHDOG_INTERVAL_S),
                ),
            )
        elif label == LAUNCH_AGENT_LABEL:
            content = render_heal_plist(
                label=label,
                program=str(program),
                config_path=str(config_path),
                throttle_interval=max(10, interval_seconds),
            )
        else:
            raise CliError(f"unknown service label: {label}", exit_code=2)
        if plist_path.is_file() and plist_path.read_text(encoding="utf-8") == content:
            existing = self.status(label=label)
            if existing.detail in ("loaded", "running"):
                return replace(existing, interval_seconds=interval_seconds)
        from maccluster.adapters.filesystem import FileSystem

        writer = self._fs_write or FileSystem().write_text_atomic
        writer(plist_path, content, mode=0o644)

        uid = os.getuid()
        domain = f"gui/{uid}"
        # bootout first for idempotent reinstall
        self._runner.run(
            ["launchctl", "bootout", domain, str(plist_path)],
            timeout=TIMEOUT_GENERIC,
        )
        load = self._runner.run(
            ["launchctl", "bootstrap", domain, str(plist_path)],
            timeout=TIMEOUT_GENERIC,
        )
        if load.returncode != 0 and "already" not in (load.stderr + load.stdout).lower():
            # try enable + kickstart
            self._runner.run(
                ["launchctl", "enable", f"{domain}/{label}"],
                timeout=TIMEOUT_GENERIC,
            )
            kick = self._runner.run(
                ["launchctl", "bootstrap", domain, str(plist_path)],
                timeout=TIMEOUT_GENERIC,
            )
            if kick.returncode != 0:
                raise CliError(
                    f"LaunchAgent {label} is installed but could not be loaded: "
                    f"{(kick.stderr or kick.stdout or 'launchctl bootstrap failed').strip()}",
                    exit_code=1,
                )

        state = self.status(label=label)
        if state.detail == "installed (not loaded)":
            raise CliError(f"LaunchAgent {label} was not loaded after installation", exit_code=1)
        return ServiceState(
            label=label,
            installed=state.installed,
            running=state.running,
            plist_path=state.plist_path,
            interval_seconds=interval_seconds,
            detail=state.detail,
        )

    def uninstall(self, *, label: str = LAUNCH_AGENT_LABEL) -> ServiceState:
        plist_path = self._plist_path(label)
        uid = os.getuid()
        domain = f"gui/{uid}"
        self._runner.run(
            ["launchctl", "bootout", domain, str(plist_path)],
            timeout=TIMEOUT_GENERIC,
        )
        if plist_path.exists() and not plist_path.is_symlink():
            plist_path.unlink()
        elif plist_path.is_symlink():
            raise CliError(f"refusing to remove symlink: {plist_path}", exit_code=2)
        return ServiceState(
            label=label,
            installed=False,
            running=False,
            plist_path=str(plist_path),
            detail="not installed",
        )

    def status(self, *, label: str = LAUNCH_AGENT_LABEL) -> ServiceState:
        plist_path = self._plist_path(label)
        installed = plist_path.is_file()
        running = False
        detail = "not installed"
        if installed:
            detail = "installed"
            uid = os.getuid()
            r = self._runner.run(
                ["launchctl", "print", f"gui/{uid}/{label}"],
                timeout=TIMEOUT_GENERIC,
            )
            if r.returncode == 0:
                running = "state = running" in r.stdout or "pid =" in r.stdout.lower()
                detail = "running" if running else "loaded"
            else:
                detail = "installed (not loaded)"
        return ServiceState(
            label=label,
            installed=installed,
            running=running,
            plist_path=str(plist_path) if installed else None,
            detail=detail,
        )


class FakeService:
    def __init__(self) -> None:
        self.states: dict[str, ServiceState] = {}

    def install(self, **kwargs) -> ServiceState:
        label = kwargs.get("label", LAUNCH_AGENT_LABEL)
        state = ServiceState(
            label=label,
            installed=True,
            running=True,
            plist_path=f"/tmp/{label}.plist",
            interval_seconds=kwargs.get("interval_seconds", 30),
            detail="installed",
        )
        self.states[label] = state
        return state

    def uninstall(self, **kwargs) -> ServiceState:
        label = kwargs.get("label", LAUNCH_AGENT_LABEL)
        self.states.pop(label, None)
        return self.status(label=label)

    def status(self, **kwargs) -> ServiceState:
        label = kwargs.get("label", LAUNCH_AGENT_LABEL)
        return self.states.get(
            label,
            ServiceState(
                label=label,
                installed=False,
                running=False,
                plist_path=None,
                detail="not installed",
            ),
        )
