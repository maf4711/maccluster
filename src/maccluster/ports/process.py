"""Process runner port."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ProcessResult:
    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False


class ProcessRunnerPort(Protocol):
    def run(
        self,
        argv: Sequence[str],
        *,
        timeout: float,
        check: bool = False,
    ) -> ProcessResult: ...

    def run_pipe(
        self,
        producer: Sequence[str],
        consumer: Sequence[str],
        *,
        timeout: float,
    ) -> ProcessResult: ...

    def resolve(self, basename: str) -> str:
        """Return absolute path for allowlisted basename."""
        ...
