"""Process-wide run deadline: a daemon thread that calls `on_expire` once `seconds` have passed.

The CLI arms it around one `docsync sync`/`plan`; on expiry it ends the whole process (exit 4)
after killing this process's own descendants (rsync, ssh). Nothing inside the sync engine knows
about it — a hang anywhere in the process is covered."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path

DEFAULT_MAX_RUNTIME = 1800  # seconds; the first full sync on 2026-11-07 moved 15 GB in 65 s
EXIT_DEADLINE = 4

_cancel: threading.Event | None = None
_limit: float = 0.0


def arm(seconds: float, on_expire) -> bool:
    """Start the deadline. seconds <= 0 means no limit (nothing armed). Re-arming replaces it."""
    global _cancel, _limit
    disarm()
    if not seconds or seconds <= 0:
        return False
    cancel = _cancel = threading.Event()
    _limit = float(seconds)

    def run():
        if not cancel.wait(seconds):
            on_expire()

    threading.Thread(target=run, name="docsync-deadline", daemon=True).start()
    return True


def disarm() -> None:
    global _cancel, _limit
    if _cancel is not None:
        _cancel.set()
        _cancel = None
    _limit = 0.0


def limit() -> float:
    """Seconds of the armed deadline; 0 when none is armed."""
    return _limit if active() else 0.0


def log_dir() -> Path:
    return Path(os.environ.get("DOCSYNC_LOG_DIR", "~/Library/Logs/docsync")).expanduser()


def dump_stacks(path: Path) -> Path:
    """Write the stack of every Python thread (name, ident, frames) to `path`; returns it."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    names = {t.ident: (t.name, t.daemon) for t in threading.enumerate()}
    out = [f"docsync pid {os.getpid()}  {time.strftime('%Y-%m-%d %H:%M:%S')}  {' '.join(sys.argv)}"]
    for ident, frame in sys._current_frames().items():
        name, daemon = names.get(ident, ("?", False))
        out.append(f"\nThread {name} (ident {ident}{', daemon' if daemon else ''}), most recent call last:")
        out.append("".join(traceback.format_stack(frame)).rstrip())
    path.write_text("\n".join(out) + "\n")
    return path


def active() -> bool:
    return _cancel is not None and not _cancel.is_set()


def descendants(pid: int | None = None) -> list[int]:
    """PIDs of every process below `pid` (default: this process), children first."""
    root = os.getpid() if pid is None else pid
    try:
        out = subprocess.run(["ps", "-A", "-o", "pid=,ppid="], text=True, capture_output=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    kids: dict[int, list[int]] = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
            kids.setdefault(int(parts[1]), []).append(int(parts[0]))
    found, todo = [], list(kids.get(root, []))
    while todo:
        p = todo.pop(0)
        if p not in found and p != root:
            found.append(p)
            todo += kids.get(p, [])
    return found


def kill_descendants(grace: float = 1.0) -> None:
    """SIGTERM this process's descendants, SIGKILL whatever is left after `grace` seconds.
    Never signals anything outside this process's own tree."""
    pids = descendants()
    _signal(pids, signal.SIGTERM)
    end = time.monotonic() + grace
    while time.monotonic() < end and _alive(pids):
        time.sleep(0.05)
    _signal([p for p in pids if _alive([p])], signal.SIGKILL)


def _signal(pids: list[int], sig: int) -> None:
    for p in pids:
        try:
            os.kill(p, sig)
        except OSError:
            pass


def _alive(pids: list[int]) -> bool:
    for p in pids:
        try:
            if os.waitpid(p, os.WNOHANG) != (0, 0):
                continue  # our own child, now reaped
        except ChildProcessError:
            pass  # not our direct child (or already reaped): fall back to a signal probe
        try:
            os.kill(p, 0)
            return True
        except OSError:
            continue
    return False
