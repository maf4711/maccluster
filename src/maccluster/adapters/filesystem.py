"""Filesystem adapter: atomic write, 0600, symlink policy."""

from __future__ import annotations

import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from maccluster.errors import CliError


class FileSystem:
    def read_text(self, path: Path) -> str:
        return path.read_text(encoding="utf-8")

    def exists(self, path: Path) -> bool:
        return path.exists()

    def is_symlink(self, path: Path) -> bool:
        try:
            return path.is_symlink()
        except OSError:
            return False

    def mkdir_parents(self, path: Path, *, mode: int = 0o700) -> None:
        path.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(path, mode)
        except OSError:
            pass

    def remove(self, path: Path) -> None:
        if path.exists() or path.is_symlink():
            path.unlink()

    def size(self, path: Path) -> int:
        return path.stat().st_size

    def write_text_atomic(
        self,
        path: Path,
        content: str,
        *,
        mode: int = 0o600,
        backup: bool = False,
    ) -> Path | None:
        path = path.expanduser()
        parent = path.parent
        self.mkdir_parents(parent)

        # Symlink policy: refuse if target is a symlink
        if path.exists() or path.is_symlink():
            if path.is_symlink():
                raise CliError(
                    f"refusing to write through symlink: {path}",
                    exit_code=2,
                )

        backup_path: Path | None = None
        if backup and path.exists():
            ts = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
            backup_path = path.with_suffix(path.suffix + f".{ts}.bak")
            # Prefer simple .bak if free
            simple = Path(str(path) + ".bak")
            if not simple.exists():
                backup_path = simple
            path.replace(backup_path)

        fd, tmp_name = tempfile.mkstemp(prefix=".maccluster-", dir=str(parent))
        tmp_path = Path(tmp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(content)
                fh.flush()
                os.fsync(fh.fileno())
            os.chmod(tmp_path, mode)
            # Re-check symlink race
            if path.is_symlink():
                tmp_path.unlink(missing_ok=True)
                raise CliError(
                    f"refusing to write through symlink: {path}",
                    exit_code=2,
                )
            os.replace(tmp_path, path)
        except Exception:
            tmp_path.unlink(missing_ok=True)
            raise
        return backup_path
