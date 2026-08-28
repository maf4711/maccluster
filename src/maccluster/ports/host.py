"""Host snapshot port (RAM / load / disk / thermal / NTP)."""

from __future__ import annotations

from typing import Protocol

from maccluster.domain.models import HostSnapshot


class HostPort(Protocol):
    def snapshot(self, node_id: str) -> HostSnapshot: ...
