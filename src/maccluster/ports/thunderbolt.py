"""Thunderbolt probe port."""

from __future__ import annotations

from typing import Protocol

from maccluster.domain.models import ThunderboltSnapshot


class ThunderboltProbePort(Protocol):
    def probe(self) -> ThunderboltSnapshot: ...
