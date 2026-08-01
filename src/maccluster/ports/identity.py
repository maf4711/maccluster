"""Host identity port."""

from __future__ import annotations

from typing import Protocol

from maccluster.domain.models import HostIdentity


class IdentityPort(Protocol):
    def get_identity(self) -> HostIdentity: ...
