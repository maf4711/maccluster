"""Mutate file lock port."""

from __future__ import annotations

from contextlib import AbstractContextManager
from pathlib import Path
from typing import Protocol


class LockPort(Protocol):
    def acquire(self, path: Path, *, timeout: float = 10.0) -> AbstractContextManager[None]: ...
