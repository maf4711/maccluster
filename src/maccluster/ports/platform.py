"""Platform info port."""

from __future__ import annotations

from typing import Protocol

from maccluster.domain.models import PlatformInfo


class PlatformPort(Protocol):
    def get_platform(self) -> PlatformInfo: ...
