"""File lock for mutate operations (PID + stale takeover)."""

from __future__ import annotations

import contextlib
import os
import time
from collections.abc import Iterator
from pathlib import Path

from maccluster.errors import CliError


class FileLock:
    def acquire(
        self, path: Path, *, timeout: float = 10.0
    ) -> contextlib.AbstractContextManager[None]:
        return _LockCtx(path, timeout=timeout)


class _LockCtx:
    def __init__(self, path: Path, *, timeout: float) -> None:
        self.path = path.expanduser()
        self.timeout = timeout
        self._fd: int | None = None

    def __enter__(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.is_symlink():
            raise CliError(f"refusing lock through symlink: {self.path}", exit_code=2)
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                self._fd = os.open(
                    str(self.path),
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                    0o600,
                )
                os.write(self._fd, f"{os.getpid()}\n{time.time()}\n".encode())
                return
            except FileExistsError:
                if self._try_stale():
                    continue
                if time.monotonic() >= deadline:
                    raise CliError(
                        f"mutate lock in progress: {self.path}",
                        exit_code=1,
                    ) from None
                time.sleep(0.1)

    def __exit__(self, *exc: object) -> None:
        if self._fd is not None:
            try:
                os.close(self._fd)
            except OSError:
                pass
            self._fd = None
        try:
            self.path.unlink(missing_ok=True)
        except OSError:
            pass

    def _try_stale(self) -> bool:
        try:
            text = self.path.read_text(encoding="utf-8")
            lines = text.strip().splitlines()
            if not lines:
                self.path.unlink(missing_ok=True)
                return True
            pid = int(lines[0].strip())
            # If process gone, take over
            try:
                os.kill(pid, 0)
                return False
            except OSError:
                self.path.unlink(missing_ok=True)
                return True
        except Exception:
            return False


@contextlib.contextmanager
def null_lock() -> Iterator[None]:
    yield
