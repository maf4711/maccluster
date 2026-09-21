"""Kernel-backed mutation lock; the persistent inode must never be unlinked."""

from __future__ import annotations

import contextlib
import fcntl
import os
import stat
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
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
        self._fd = fd
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise CliError(f"lock must be a regular file: {self.path}", exit_code=2)
            deadline = time.monotonic() + self.timeout
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise CliError(
                            f"mutate lock in progress: {self.path}", exit_code=1
                        ) from None
                    time.sleep(min(0.1, max(0.0, deadline - time.monotonic())))
            os.fchmod(fd, 0o600)
            os.ftruncate(fd, 0)
            os.write(fd, f"{os.getpid()}\n{time.time()}\n".encode())
        except BaseException:
            self.__exit__()
            raise

    def __exit__(self, *exc: object) -> None:
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None
        # Closing releases the kernel lock, including on process death.
        # Unlinking would let waiters acquire distinct inodes simultaneously.


@contextlib.contextmanager
def null_lock() -> Iterator[None]:
    yield
