"""Run lock shared by the CLI and docsync.api (flock on <state>/run.lock).

While a run holds the lock, run.lock names it: "<pid>\\n<unix seconds>\\n<command line>\\n".
The file is opened without truncation and rewritten only after flock succeeds, so a caller
that fails to get the lock never touches the holder's record."""

from __future__ import annotations

import fcntl
import os
import shlex
import sys
import time
from pathlib import Path

MAX_RECORD = 4096


def _record() -> bytes:
    head = f"{os.getpid()}\n{time.time():.3f}\n".encode()
    cmd = shlex.join(sys.argv).replace("\n", " ").encode()[: MAX_RECORD - len(head) - 1]
    return head + cmd.decode(errors="ignore").encode() + b"\n"


def acquire(state_dir: Path, wait: float = 0.0):
    """Open file handle holding the lock, or None after `wait` seconds. Never exits."""
    state_dir.mkdir(parents=True, exist_ok=True)
    fh = os.fdopen(os.open(state_dir / "run.lock", os.O_RDWR | os.O_CREAT, 0o644), "r+b", buffering=0)
    deadline = time.monotonic() + wait
    while True:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except OSError:
            if time.monotonic() >= deadline:
                fh.close()
                return None
            time.sleep(0.5)
    try:
        fh.truncate(0)
        os.pwrite(fh.fileno(), _record(), 0)
    except OSError:
        pass  # the record is informational; the flock is what excludes other runs
    return fh


def holder(state_dir: Path) -> dict | None:
    """The record of the run that holds (or last held) the lock; None if empty or unreadable."""
    try:
        pid, started, command = (Path(state_dir) / "run.lock").read_text(errors="replace").split("\n")[:3]
        return {"pid": int(pid), "started": float(started), "command": command}
    except (OSError, ValueError):
        return None


def busy_message(state_dir: Path) -> str:
    """'another run is in progress (pid N, running M min, cmd)' — the bare text without a record."""
    h = holder(state_dir)
    if h is None:
        return "another run is in progress"
    mins = max(0, int((time.time() - h["started"]) // 60))
    return f"another run is in progress (pid {h['pid']}, running {mins} min, {h['command']})"
