"""Ends a peer's running rsync processes once its peer lock is lost in the middle of a push or pull.

session wraps each transfer in `Watch(peer_lock)`; transport registers every rsync it starts with
track()/untrack(). The current Watch travels in a context variable, so rsync streams started from
worker threads (transport.run_rsync_streams copies the context) belong to the right peer. Only the
Popen children registered here are ever signalled."""

from __future__ import annotations

import contextvars
import subprocess
import sys
import threading
import time

from docsync import lock

_current: contextvars.ContextVar[Watch | None] = contextvars.ContextVar("docsync_xferwatch", default=None)


class Watch:
    """While entered: checks peer_lock.held() every heartbeat/4; on False sets `lost` and ends the
    tracked rsyncs (SIGTERM, SIGKILL after one heartbeat). Without a peer lock it only tracks."""

    def __init__(self, peer_lock: lock.PeerLock | None):
        self.peer_lock, self.lost = peer_lock, False
        self._procs: set[subprocess.Popen] = set()
        self._mu, self._stop = threading.Lock(), threading.Event()
        self._thread: threading.Thread | None = None

    def __enter__(self) -> Watch:
        self._token = _current.set(self)
        if self.peer_lock is not None:
            self._thread = threading.Thread(target=self._run, name="docsync-xferwatch", daemon=True)
            self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join()
        _current.reset(self._token)

    def _run(self) -> None:
        beat = lock.heartbeat_s()
        while not self._stop.wait(beat / 4):
            try:
                held = self.peer_lock.held()
            except Exception as e:  # fail closed: an unknown lock state is a lost lock
                print(f"docsync: peer lock check failed, treated as lost: {e!r}", file=sys.stderr, flush=True)
                held = False
            if not held:
                with self._mu:
                    self.lost = True
                    procs = list(self._procs)
                end_all(procs, beat)
                return

    def add(self, proc: subprocess.Popen) -> None:
        with self._mu:
            self._procs.add(proc)
            lost = self.lost
        if lost:  # started after the loss (a retry, a late stream): never let it run
            proc.kill()

    def discard(self, proc: subprocess.Popen) -> None:
        with self._mu:
            self._procs.discard(proc)


def end_all(procs: list[subprocess.Popen], grace: float) -> None:
    """SIGTERM every proc, then SIGKILL those still running `grace` seconds later (one shared deadline,
    so N streams that ignore SIGTERM still end within one grace). Popen skips a child it already reaped."""
    for p in procs:
        p.terminate()
    deadline = time.monotonic() + grace
    for p in procs:
        try:
            p.wait(max(0.0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            p.kill()


def track(proc: subprocess.Popen) -> None:
    w = _current.get()
    if w is not None:
        w.add(proc)


def untrack(proc: subprocess.Popen) -> None:
    w = _current.get()
    if w is not None:
        w.discard(proc)
