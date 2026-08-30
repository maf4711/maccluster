"""Killable directory listing for the local inventory walk — one helper, not one per folder.

``os.scandir`` on a wedged iCloud / FileProvider directory blocks in a way that
ignores SIGALRM, so the listing has to happen in a process we can kill. Doing
that *per directory* costs one interpreter start per folder: measured on
``~/Documents/Dokumente – CM-…`` (97 675 directories, 261 949 files) that was
~835 files/s against ~13 400 files/s for a plain in-process walk — the entire
gap is ``python -c`` startup, paid 97 675 times.

This module pays it once. One long-lived helper reads directory paths from
stdin (one JSON string per line) and answers with one JSON line per request.
A request that outruns its timeout kills the helper — the hang stays killable —
the directory is reported as skipped, and the next request starts a fresh one.
"""

from __future__ import annotations

import json
import os
import select
import subprocess
import sys
import time
from collections.abc import Sequence
from types import TracebackType

# name, absolute path, is_dir, is_file — same shape the walk consumed before
DirEntryRow = tuple[str, str, bool, bool]

REASON_TIMEOUT = "timeout"  # helper never answered → killed; coverage is truncated
REASON_UNREADABLE = "unreadable"  # scandir raised (gone, no permission) — stable
REASON_WORKER = "worker-failed"  # could not spawn / talk to the helper


# Answers are pure ASCII JSON (ensure_ascii escapes non-ASCII and newlines), so
# one request is always exactly one line even for names containing "\n".
_WORKER_SRC = """\
import json, os, sys

out = sys.stdout
while True:
    line = sys.stdin.readline()
    if not line:
        break
    line = line.strip()
    if not line:
        continue
    try:
        path = json.loads(line)
    except Exception:
        out.write('{"err": "bad-request"}\\n')
        out.flush()
        continue
    rows = []
    try:
        with os.scandir(path) as it:
            for e in it:
                try:
                    rows.append([
                        e.name,
                        e.path,
                        e.is_dir(follow_symlinks=False),
                        e.is_file(follow_symlinks=False),
                    ])
                except OSError:
                    pass
    except Exception as exc:
        out.write(json.dumps({"err": type(exc).__name__}) + "\\n")
        out.flush()
        continue
    out.write(json.dumps({"rows": rows}) + "\\n")
    out.flush()
"""


class ScandirWorker:
    """A reusable, killable directory lister.

    ``listdir`` returns the entries, or ``None`` when the directory could not
    be listed; ``last_reason`` then says why. ``REASON_TIMEOUT`` means the
    listing hung and was killed — the caller has *not* seen that subtree and
    must treat its inventory as incomplete. ``REASON_UNREADABLE`` is an
    ordinary OSError (vanished, no permission) and is a stable property of the
    tree rather than a truncation.
    """

    def __init__(
        self,
        *,
        timeout_s: float = 6.0,
        argv: Sequence[str] | None = None,
    ) -> None:
        self.timeout_s = max(0.05, float(timeout_s))
        self._argv: list[str] = (
            list(argv) if argv else [sys.executable or "python3", "-c", _WORKER_SRC]
        )
        self._proc: subprocess.Popen[bytes] | None = None
        self._buf = b""
        self.starts = 0  # helper processes spawned — 1 for a healthy walk
        self.restarts = 0  # helpers killed and replaced
        self.last_reason = ""

    # ---------------------------------------------------------------- lifecycle
    def __enter__(self) -> ScandirWorker:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        self._discard()

    def _spawn(self) -> subprocess.Popen[bytes] | None:
        try:
            proc = subprocess.Popen(  # noqa: S603 — fixed argv, no shell
                self._argv,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                close_fds=True,
            )
        except OSError:
            return None
        self.starts += 1
        self._buf = b""
        self._proc = proc
        return proc

    def _discard(self) -> None:
        """Kill the helper and forget it; a wedged scandir only dies on SIGKILL."""
        proc, self._proc = self._proc, None
        self._buf = b""
        if proc is None:
            return
        for stream in (proc.stdin, proc.stdout):
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass
        try:
            proc.kill()
        except OSError:
            pass
        try:
            proc.wait(timeout=2.0)
        except Exception:
            pass  # unreapable (hung on a network FS) — bounded, move on

    # ------------------------------------------------------------------ listing
    def listdir(self, path: str | os.PathLike[str]) -> list[DirEntryRow] | None:
        self.last_reason = ""
        target = os.fspath(path)
        proc = self._proc
        if proc is None or proc.poll() is not None:
            if proc is not None:
                self._discard()
            proc = self._spawn()
        if proc is None or proc.stdin is None or proc.stdout is None:
            self.last_reason = REASON_WORKER
            return None

        try:
            proc.stdin.write(json.dumps(target).encode("ascii") + b"\n")
            proc.stdin.flush()
        except (OSError, ValueError):
            self._discard()
            self.restarts += 1
            self.last_reason = REASON_WORKER
            return None

        line = self._read_line(time.monotonic() + self.timeout_s)
        if line is None:
            # Hung or died mid-listing: kill it, report the directory as skipped.
            self._discard()
            self.restarts += 1
            self.last_reason = REASON_TIMEOUT
            return None
        try:
            answer = json.loads(line)
        except ValueError:
            self._discard()
            self.restarts += 1
            self.last_reason = REASON_WORKER
            return None
        if not isinstance(answer, dict) or not isinstance(answer.get("rows"), list):
            self.last_reason = REASON_UNREADABLE
            return None
        return [
            (str(row[0]), str(row[1]), bool(row[2]), bool(row[3]))
            for row in answer["rows"]
            if isinstance(row, list) and len(row) >= 4
        ]

    def _read_line(self, deadline: float) -> str | None:
        """One answer line, or None if the deadline passes / the helper dies."""
        proc = self._proc
        if proc is None or proc.stdout is None:
            return None
        fd = proc.stdout.fileno()
        while True:
            nl = self._buf.find(b"\n")
            if nl >= 0:
                line = self._buf[:nl]
                self._buf = self._buf[nl + 1 :]
                return line.decode("utf-8", "replace")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            try:
                ready, _, _ = select.select([fd], [], [], remaining)
            except (OSError, ValueError):
                return None
            if not ready:
                return None
            try:
                chunk = os.read(fd, 1 << 16)
            except OSError:
                return None
            if not chunk:
                return None  # helper exited
            self._buf += chunk
