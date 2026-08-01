"""Bandwidth bench port."""

from __future__ import annotations

from typing import Protocol

from maccluster.domain.models import BenchResult


class BenchPort(Protocol):
    def available(self) -> bool: ...

    def run(self, target: str, *, duration: int = 5) -> BenchResult: ...
