"""Run lock shared by the CLI and docsync.api (flock on <state>/run.lock)."""

from __future__ import annotations

import fcntl
import time
from pathlib import Path


def acquire(state_dir: Path, wait: float = 0.0):
    """Open file handle holding the lock, or None after `wait` seconds. Never exits."""
    state_dir.mkdir(parents=True, exist_ok=True)
    fh = open(state_dir / "run.lock", "w")
    deadline = time.monotonic() + wait
    while True:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fh
        except OSError:
            if time.monotonic() >= deadline:
                fh.close()
                return None
            time.sleep(0.5)
