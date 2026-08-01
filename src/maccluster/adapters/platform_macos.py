"""Platform detection adapter."""

from __future__ import annotations

import platform
import sys

from maccluster.constants import TIMEOUT_GENERIC
from maccluster.domain.models import PlatformInfo
from maccluster.ports.process import ProcessRunnerPort


class MacOSPlatform:
    def __init__(self, runner: ProcessRunnerPort | None = None) -> None:
        self._runner = runner

    def get_platform(self) -> PlatformInfo:
        is_macos = sys.platform == "darwin"
        machine = platform.machine().lower()
        is_arm64 = machine in ("arm64", "aarch64")
        os_version = None
        if is_macos and self._runner is not None:
            try:
                r = self._runner.run(["sw_vers", "-productVersion"], timeout=TIMEOUT_GENERIC)
                if r.returncode == 0:
                    os_version = r.stdout.strip()
            except Exception:
                os_version = platform.mac_ver()[0] or None
        elif is_macos:
            os_version = platform.mac_ver()[0] or None
        return PlatformInfo(
            is_macos=is_macos,
            is_arm64=is_arm64,
            os_version=os_version,
            machine=machine,
        )


class FakePlatform:
    def __init__(self, *, is_macos: bool = True, is_arm64: bool = True) -> None:
        self._info = PlatformInfo(
            is_macos=is_macos,
            is_arm64=is_arm64,
            os_version="15.0",
            machine="arm64" if is_arm64 else "x86_64",
        )

    def get_platform(self) -> PlatformInfo:
        return self._info
