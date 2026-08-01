"""LaunchAgent service port."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from maccluster.domain.models import ServiceState


class ServicePort(Protocol):
    def install(
        self,
        *,
        program: Path,
        config_path: Path,
        interval_seconds: int,
        label: str,
    ) -> ServiceState: ...

    def uninstall(self, *, label: str) -> ServiceState: ...

    def status(self, *, label: str) -> ServiceState: ...
