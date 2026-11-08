"""Run lock shared by the CLI and docsync.api (flock on <state>/run.lock), and the per-root locks.

While a run holds the lock, run.lock names it: "<pid>\\n<unix seconds>\\n<command line>\\n".
The file is opened without truncation and rewritten only after flock succeeds, so a caller
that fails to get the lock never touches the holder's record.

Root locks (<state>/locks/root-<sha1(real root path)[:12]>.lock) carry the same record plus a 4th
line, the host name of the run. A sync holds one on this Mac and one on the peer (taken there by
PEERLOCK_PY over ssh) for the whole run, so two Macs never sync the same pair at once."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import select
import shlex
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

MAX_RECORD = 4096
LOCK_WAIT = 1.0  # seconds per root lock; the order of the two locks (see RootLocks) avoids deadlock


def host_name() -> str:
    return socket.gethostname().split(".")[0]


def record_fields() -> dict:
    """This run as a holder: pid, start time, command line, host (the 4th line of a root lock)."""
    return {"pid": os.getpid(), "started": round(time.time(), 3),
            "command": shlex.join(sys.argv).replace("\n", " "), "host": host_name()}


def encode_record(rec: dict, host: bool = True) -> bytes:
    """The holder lines, at most MAX_RECORD bytes (the command is cut, never the other lines)."""
    head = f"{rec['pid']}\n{float(rec['started']):.3f}\n".encode()
    name = str(rec.get("host") or "").replace("\n", " ")[:255] if host else ""
    tail = f"{name}\n".encode() if name else b""
    cmd = str(rec["command"]).replace("\n", " ").encode()[: MAX_RECORD - len(head) - len(tail) - 1]
    return head + cmd.decode(errors="ignore").encode() + b"\n" + tail  # drop a UTF-8 sequence cut in half


def root_lock_path(state_dir: Path, root: Path) -> Path:
    real = os.path.realpath(os.path.expanduser(str(root)))
    return Path(state_dir) / "locks" / f"root-{hashlib.sha1(real.encode()).hexdigest()[:12]}.lock"


def acquire(state_dir: Path, wait: float = 0.0):
    """Open file handle holding the lock, or None after `wait` seconds. Never exits."""
    return _acquire(Path(state_dir) / "run.lock", wait, encode_record(record_fields(), host=False))  # 3-line record


def acquire_root(state_dir: Path, root: Path, wait: float = 0.0):
    """Like acquire(), for the root lock of `root` (any path; compared by its real path)."""
    return _acquire(root_lock_path(state_dir, root), wait, encode_record(record_fields()))


def _acquire(path: Path, wait: float, record: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    fh = os.fdopen(os.open(path, os.O_RDWR | os.O_CREAT, 0o644), "r+b", buffering=0)
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
        os.pwrite(fh.fileno(), record, 0)
    except OSError:
        pass  # the record is informational; the flock is what excludes other runs
    return fh


def holder(state_dir: Path) -> dict | None:
    """The record of the run that holds (or last held) the lock; None if empty or unreadable."""
    return read_holder(Path(state_dir) / "run.lock")


def read_holder(path: Path) -> dict | None:
    """Holder record of a lock file; the key "host" only when the record has a 4th line."""
    try:
        lines = Path(path).read_text(errors="replace").split("\n")
        rec = {"pid": int(lines[0]), "started": float(lines[1]), "command": lines[2]}
    except (OSError, ValueError, IndexError):
        return None
    if len(lines) > 3 and lines[3].strip():
        rec["host"] = lines[3].strip()
    return rec


def busy_text(h: dict | None) -> str:
    """'another run is in progress (pid N[ on HOST], running M min, cmd)' — the bare text without a record."""
    if h is None:
        return "another run is in progress"
    mins = max(0, int((time.time() - h["started"]) // 60))
    return f"another run is in progress (pid {h['pid']}{_on_host(h)}, running {mins} min, {h['command']})"


def local_busy_text(h: dict | None) -> str:
    """This Mac's root lock is held by another run (another pair's, from a peer): skip, retry next run."""
    who = f" (pid {h['pid']}{_on_host(h)})" if h else ""
    return f"local root busy{who} — retried next run"


def _on_host(h: dict) -> str:
    return f" on {h['host']}" if h.get("host") else ""


def busy_message(state_dir: Path) -> str:
    return busy_text(holder(state_dir))


def root_busy_message(state_dir: Path, root: Path) -> str:
    return busy_text(read_holder(root_lock_path(state_dir, root)))


# ── the peer side ──────────────────────────────────────────────────────────────────────────────
# Runs on the peer under Python 3.9 (stdlib only). argv: N root state heartbeat cap. Prints
# "key <host>:<real root>", waits for "lock <wait> <json record text>", answers "locked" or
# "busy <json holder>", then holds the flock (answering each "hb" with "ok") until stdin closes,
# stays silent for 3 heartbeats, says "release", or `cap` seconds (0 = none) have passed.
PEERLOCK_PY = r"""
import fcntl, hashlib, json, os, select, socket, sys, time
root, state, hb, cap = sys.argv[2], sys.argv[3], float(sys.argv[4]), float(sys.argv[5])
end = time.monotonic() + cap if cap > 0 else None
root = os.path.realpath(os.path.expanduser(root))
buf = [b""]

def say(text):
    try:
        os.write(1, (text + "\n").encode())
    except OSError:
        pass  # the client is gone: the next read sees EOF and the helper ends

def line():
    while b"\n" not in buf[0]:
        left = 3 * hb if end is None else min(3 * hb, end - time.monotonic())
        if left <= 0 or not select.select([0], [], [], left)[0]:
            return None
        chunk = os.read(0, 4096)
        if not chunk:
            return None
        buf[0] += chunk
    ln, buf[0] = buf[0].split(b"\n", 1)
    return ln.decode("utf-8", "replace")

def holder(path):
    try:
        with open(path, errors="replace") as fh:
            lines = fh.read().split("\n")
        h = {"pid": int(lines[0]), "started": float(lines[1]), "command": lines[2]}
    except (OSError, ValueError, IndexError):
        return None
    if len(lines) > 3 and lines[3].strip():
        h["host"] = lines[3].strip()
    return h

def main():
    say("key %s:%s" % (socket.gethostname().split(".")[0], root))
    req = line()
    if not req or not req.startswith("lock "):
        return
    _, wait, rec = req.split(" ", 2)
    name = "root-%s.lock" % hashlib.sha1(root.encode()).hexdigest()[:12]
    path = os.path.join(os.path.expanduser(state), "locks", name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    give_up = time.monotonic() + float(wait)
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except OSError:
            if time.monotonic() >= give_up:
                say("busy " + json.dumps(holder(path)))
                return
            time.sleep(0.1)
    try:
        os.ftruncate(fd, 0)
        os.pwrite(fd, json.loads(rec).encode()[:4096].decode("utf-8", "ignore").encode(), 0)
    except (OSError, ValueError):
        pass
    say("locked")
    while True:
        ln = line()
        if ln is None or ln == "release":
            return  # exiting closes fd, which frees the flock
        if ln == "hb":
            say("ok")  # the client's proof that the lock is still held here

main()
"""
# Reads exactly N bytes of PEERLOCK_PY from stdin, so the rest of stdin stays the heartbeat channel.
_BOOT = 'import os,sys;exec(b"".join(os.read(0,1) for _ in range(int(sys.argv[1]))).decode())'


class LockUnavailable(Exception):
    """The peer helper did not start or answer (no python3 there, ssh failed)."""


def heartbeat_s() -> float:
    try:
        return max(0.05, float(os.environ.get("DOCSYNC_LOCK_HEARTBEAT_S", "10")))
    except ValueError:
        return 10.0


def peer_lock_command(root_expr: str, cap: float) -> str:
    """Remote shell command for the helper; root_expr is already shell-quoted (transport.remote_path)."""
    state = '"${DOCSYNC_STATE_DIR:-$HOME/.local/state/docsync}"'
    n = len(PEERLOCK_PY.encode())
    return (f"command -v python3 >/dev/null 2>&1 && python3 -c {shlex.quote(_BOOT)} {n} {root_expr} {state} "
            f"{heartbeat_s():g} {cap:g}")


class PeerLock:
    """The root lock on the peer: PEERLOCK_PY in one long-lived ssh session (`argv`), kept by a
    heartbeat line every heartbeat_s() seconds that the helper acknowledges with "ok". Ends when
    release() closes stdin or this process dies. held() says whether the lock is still ours."""

    def __init__(self, argv: list[str], timeout: float = 20.0):
        self.argv, self.key, self._buf, self._eof = argv, "", b"", False
        self._stop, self._mu = threading.Event(), threading.Lock()
        self.last_ack = 0.0  # wall clock (time.time) of the last proof from the peer: sleep counts
        self._threads: list[threading.Thread] = []
        try:
            self.proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                         stderr=subprocess.DEVNULL)
            self.proc.stdin.write(PEERLOCK_PY.encode())
            self.proc.stdin.flush()
        except OSError as e:
            raise LockUnavailable(str(e)) from None
        ln = self._line(timeout)
        if not ln or not ln.startswith("key "):
            self.release()
            raise LockUnavailable("no answer from the lock helper (python3 on the peer?)")
        self.key = ln[4:]

    def _line(self, timeout: float) -> str | None:
        fd, end = self.proc.stdout.fileno(), time.monotonic() + timeout
        while b"\n" not in self._buf:
            left = end - time.monotonic()
            if left <= 0 or not select.select([fd], [], [], left)[0]:
                return None
            chunk = os.read(fd, 4096)
            if not chunk:
                self._eof = True
                return None
            self._buf += chunk
        ln, self._buf = self._buf.split(b"\n", 1)
        return ln.decode(errors="replace")

    def _send(self, text: str) -> None:
        with self._mu:
            self.proc.stdin.write((text + "\n").encode())
            self.proc.stdin.flush()

    def lock(self, wait: float = LOCK_WAIT, record: bytes | None = None) -> str | None:
        """None once the peer holds the lock for this run; otherwise the busy message (and released)."""
        rec = (record or encode_record(record_fields())).decode(errors="ignore")
        try:
            self._send(f"lock {wait:g} {json.dumps(rec)}")
        except OSError as e:
            self.release()
            raise LockUnavailable(str(e)) from None
        ln = self._line(wait + 20)
        if ln == "locked":
            self.last_ack = time.time()
            return None
        self.release()
        if ln and ln.startswith("busy "):
            try:
                return busy_text(json.loads(ln[5:]))
            except ValueError:
                return busy_text(None)
        raise LockUnavailable("the lock helper gave no answer")

    def keep_alive(self) -> None:
        """Heartbeat thread (one "hb" per heartbeat_s()) and reader thread (each "ok" → last_ack).
        A failing send just stops the beat: held() then turns False on its own."""
        def beat():
            while not self._stop.wait(heartbeat_s()):
                try:
                    self._send("hb")
                except (OSError, ValueError):
                    return

        def read():
            while not self._stop.is_set() and not self._eof:
                try:
                    if self._line(0.5) == "ok":
                        self.last_ack = time.time()
                except (OSError, ValueError):
                    return
        for fn, name in ((beat, "heartbeat"), (read, "acks")):
            t = threading.Thread(target=fn, name=f"docsync-peerlock-{name}", daemon=True)
            t.start()
            self._threads.append(t)

    def held(self) -> bool:
        """True while the helper runs and acknowledged a heartbeat within 2 heartbeats (wall clock,
        so a sleeping Mac or a dead link counts against it; the helper gives up after 3)."""
        return self.proc.poll() is None and time.time() - self.last_ack < 2 * heartbeat_s()

    def release(self) -> None:
        self._stop.set()
        for t in self._threads:  # the reader must leave stdout before it is closed
            t.join(2)
        with self._mu:
            try:
                self.proc.stdin.close()
            except OSError:
                pass
        try:
            self.proc.wait(5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()
        self.proc.stdout.close()


class RootLocks:
    """The root locks of one run for one local root: this Mac's lock once (shared by every peer of
    the run, released by close()), one PeerLock per peer. Of each pair the lock whose key
    "<host>:<real root path>" sorts first is taken first, each with a wait of at most `wait`, so
    two reverse runs started together end with one run syncing and the other reporting busy."""

    def __init__(self, state_dir: Path, root: Path, wait: float = LOCK_WAIT, max_runtime: float | None = None):
        self.state_dir, self.root, self.wait = Path(state_dir), Path(root), wait
        self.max_runtime = max_runtime  # for the peer helper's hard cap (transport.root_lock)
        self.key = f"{host_name()}:{os.path.realpath(os.path.expanduser(str(root)))}"
        self._fh, self._mu = None, threading.Lock()

    def _local(self, peer_host: str = "") -> tuple[str, str] | None:
        """None once this Mac's root lock is ours. Held by the reverse run of this very pair (its
        record names the peer's host) → ("peer", …): that run covers us; otherwise ("local", …)."""
        with self._mu:
            if self._fh is None:
                self._fh = acquire_root(self.state_dir, self.root, self.wait)
                if self._fh is None:
                    h = read_holder(root_lock_path(self.state_dir, self.root))
                    if peer_host and h and h.get("host") == peer_host:
                        return "peer", busy_text(h)
                    return "local", local_busy_text(h)
        return None

    def pair(self, transport) -> tuple[PeerLock | None, tuple[str, str] | None, list[str]]:
        """(peer lock to release after this peer's run, busy or None, warnings). busy is
        ("peer", busy_text) when the peer's lock was held, ("local", text) when this Mac's was."""
        opener = getattr(transport, "root_lock", None)
        if opener is None:  # a directory on this Mac: no second process holds anything there
            return None, self._local(), []
        try:  # opener() and lock() release the helper themselves when they raise
            peer = opener(self.max_runtime)
            local_first, peer_host = self.key <= peer.key, peer.key.split(":", 1)[0]
            busy = self._local(peer_host) if local_first else None
            if busy is None:
                text = peer.lock(self.wait)
                if text is None:
                    peer.keep_alive()
                else:
                    busy = ("peer", text)
        except LockUnavailable as e:
            return None, self._local(), [f"peer lock unavailable — synced without it: {e}"]
        if busy is None and not local_first:
            busy = self._local(peer_host)
        if busy is not None:
            peer.release()
            return None, busy, []
        return peer, None, []

    def close(self) -> None:
        with self._mu:
            if self._fh is not None:
                self._fh.close()
                self._fh = None
