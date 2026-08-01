"""Filesystem port."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol


class FileSystemPort(Protocol):
    def read_text(self, path: Path) -> str: ...

    def write_text_atomic(
        self,
        path: Path,
        content: str,
        *,
        mode: int = 0o600,
        backup: bool = False,
    ) -> Path | None:
        """Write atomically. Returns backup path if created."""
        ...

    def exists(self, path: Path) -> bool: ...

    def is_symlink(self, path: Path) -> bool: ...

    def mkdir_parents(self, path: Path, *, mode: int = 0o700) -> None: ...

    def remove(self, path: Path) -> None: ...

    def size(self, path: Path) -> int: ...
