"""RDMA rung of the sync ladder: hand the plan to ``arep xfer`` as a manifest.

maccluster keeps inventory and planning (``plan_transfers``); arep moves the
bytes. The contract (spec §4.10):

    arep xfer push|pull --node <id> --manifest -     ← JSON-Lines on stdin
    stdout: JSON-Lines  {"event":"progress","done":N,"total":N}
                        {"event":"done","bytes":N}
                        {"event":"error","reason":"…"}
    exit ≠ 0 on abort

No RDMA logic lives here. The subprocess is injectable (``runner``) so the
whole path is unit-testable without arep.
"""

from __future__ import annotations

import json
import math
import os
import re
import signal
import subprocess
import tempfile
import threading
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import IO, TYPE_CHECKING, Literal, Protocol

from maccluster.constants import TIMEOUT_SYNC
from maccluster.errors import CliError
from maccluster.services.transport_ladder import (
    AREP_BIN,
    TransportFailed,
    arep_process_runner,
    clean_text,
)

if TYPE_CHECKING:
    from maccluster.services.sync_service import FileMeta

__all__ = [
    "XferRunner",
    "manifest_lines",
    "manifest_text",
    "run_rdma_transfer",
    "xfer_subprocess_runner",
]

Direction = Literal["push", "pull"]
ProgressCb = Callable[[int, int], None]  # (bytes_done, bytes_total)
LineCb = Callable[[str], None]
_STDERR_TAIL = 400
_MAX_LINE = 1 << 20  # longest stdout line we keep; JSON events are tiny
# argv safety: a node id is a plain token (never starts with "-", so arep can
# not read it as an option); the binary is the bare name or an absolute path.
_NODE_ID_RE = re.compile(r"[A-Za-z0-9._-]+")


def _check_node_id(node_id: str) -> str:
    if (
        not isinstance(node_id, str)
        or not _NODE_ID_RE.fullmatch(node_id)
        or node_id.startswith("-")
    ):
        raise ValueError(f"unsafe node id {node_id!r} (want [A-Za-z0-9._-]+, no leading '-')")
    return node_id


def _check_arep_bin(arep_bin: str) -> str:
    if not isinstance(arep_bin, str) or not arep_bin or "\x00" in arep_bin:
        raise ValueError(f"unsafe arep binary {arep_bin!r}")
    if arep_bin == AREP_BIN:
        return arep_bin
    if not arep_bin.startswith("/") or Path(arep_bin).name != AREP_BIN:
        raise ValueError(
            f"arep binary must be {AREP_BIN!r} or an absolute path to it: {arep_bin!r}"
        )
    return arep_bin


class XferRunner(Protocol):
    """Spawn *argv*, feed *stdin_text*, call *on_line* per stdout line → (rc, stderr)."""

    def __call__(
        self,
        argv: Sequence[str],
        *,
        stdin_text: str,
        on_line: LineCb,
        timeout: float,
    ) -> tuple[int, str]: ...


# --- manifest ----------------------------------------------------------------------------


def _check_rel(rel: object) -> str:
    """A manifest rel is a normalised relative path arep may join onto a home.

    Rejected: non-str, empty, absolute, any ``.``/``..``/empty component,
    control characters (incl. NUL and newline) and text that is not valid
    UTF-8 (lone surrogates from a mis-decoded name). ``ValueError`` — the
    whole rung refuses rather than dropping a file silently.
    """
    if not isinstance(rel, str) or not rel:
        raise ValueError(f"unsafe manifest rel {rel!r}: empty or not a string")
    if rel.startswith("/"):
        raise ValueError(f"unsafe manifest rel {rel!r}: absolute path")
    if any(part in ("", ".", "..") for part in rel.split("/")):
        raise ValueError(f"unsafe manifest rel {rel!r}: '.', '..' or empty component")
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in rel):
        raise ValueError(f"unsafe manifest rel {rel!r}: control character")
    rel.encode("utf-8")  # UnicodeEncodeError is a ValueError
    return rel


def manifest_lines(rels: Iterable[str], inv: Mapping[str, FileMeta]) -> Iterator[str]:
    """JSON-Lines rows ``{"rel","size","mtimeNs"}`` (no newline) in plan order.

    Every *rel* must be in *inv* (``KeyError`` otherwise) and pass
    ``_check_rel`` (``ValueError``) — a silently dropped file would look like
    a successful sync, an unchecked one could escape the peer's home.
    """
    for rel in rels:
        meta = inv[rel]
        rel = _check_rel(rel)
        yield json.dumps(
            {"rel": rel, "size": int(meta.size), "mtimeNs": int(meta.mtime_ns)},
            ensure_ascii=False,
        )


def manifest_text(rels: Iterable[str], inv: Mapping[str, FileMeta]) -> str:
    return "".join(line + "\n" for line in manifest_lines(rels, inv))


# --- progress parsing ----------------------------------------------------------------------


@dataclass
class _XferState:
    last_done: int = 0
    total: int = 0
    done_bytes: int | None = None
    error: str | None = None


def _as_int(value: object) -> int | None:
    """Non-negative finite number → int; anything else (bool, NaN, inf, <0) → None."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    out = int(value)
    return out if out >= 0 else None


def _handle_line(line: str, state: _XferState, on_progress: ProgressCb) -> None:
    text = line.strip()
    if not text.startswith("{"):
        return
    try:
        event = json.loads(text)
    except (ValueError, RecursionError):
        return
    if not isinstance(event, dict):
        return
    kind = event.get("event")
    if kind == "progress":
        done = _as_int(event.get("done"))
        if done is None:
            return
        total = _as_int(event.get("total"))
        if total is None or total < done:
            total = max(done, state.total)
        state.last_done, state.total = done, total
        on_progress(done, total)
    elif kind == "done":
        state.done_bytes = _as_int(event.get("bytes"))
    elif kind == "error" and state.error is None:
        state.error = clean_text(event.get("reason") or "arep reported error", _STDERR_TAIL)


# --- default subprocess runner ----------------------------------------------------------------


def _prepare_argv(argv: Sequence[str]) -> list[str]:
    if not argv:
        raise CliError("empty argv", exit_code=1)
    try:
        first = _check_arep_bin(argv[0])
    except ValueError as exc:
        raise CliError(f"refusing non-{AREP_BIN} binary: {exc}", exit_code=1) from exc
    if first.startswith("/"):
        return [first, *argv[1:]]
    return [arep_process_runner().resolve(first), *argv[1:]]


def _child_env() -> dict[str, str]:
    return {
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin:/usr/local/bin",
        "HOME": os.environ.get("HOME", ""),
        "USER": os.environ.get("USER", ""),
        "LANG": "C",
        "LC_ALL": "C",
    }


def _feed_stdin(proc: subprocess.Popen, data: bytes) -> None:
    assert proc.stdin is not None
    try:
        proc.stdin.write(data)
    except (BrokenPipeError, OSError):
        pass
    finally:
        try:
            proc.stdin.close()
        except OSError:
            pass


def _kill_tree(proc: subprocess.Popen) -> None:
    """SIGKILL arep and everything it spawned (own session ⇒ pgid == pid)."""
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except OSError:
        try:
            proc.kill()
        except OSError:
            pass


def _read_lines(stdout: IO[bytes], on_line: LineCb) -> None:
    """Hand complete lines to *on_line*; a line over ``_MAX_LINE`` is dropped whole.

    ``for raw in stdout`` would buffer an endless line in memory; reading with
    a cap and draining the remainder keeps a misbehaving arep from taking the
    caller down with it. Events are a few dozen bytes, so 1 MiB is generous.
    """
    while True:
        raw = stdout.readline(_MAX_LINE)
        if not raw:
            return
        if len(raw) >= _MAX_LINE and not raw.endswith(b"\n"):
            while True:  # drain the rest of the overlong line
                more = stdout.readline(_MAX_LINE)
                if not more or more.endswith(b"\n"):
                    break
            continue
        on_line(raw.decode("utf-8", errors="replace").rstrip("\r\n"))


def xfer_subprocess_runner(
    argv: Sequence[str],
    *,
    stdin_text: str,
    on_line: LineCb,
    timeout: float,
) -> tuple[int, str]:
    """Default ``XferRunner``: Popen, manifest on stdin, live stdout lines.

    stdin is written from a helper thread so a chatty arep cannot deadlock
    against an unread pipe; stderr goes to a temp file for the same reason.
    The manifest is encoded *before* the spawn (an unencodable name must not
    start arep with an empty manifest). arep runs in its own session so the
    watchdog — and any exception from *on_line* — kills the whole tree; a
    grandchild holding stdout could otherwise stall the reader forever.
    A kill on *timeout* reports rc 124 like ``ProcessRunner``; a non-positive
    timeout falls back to ``TIMEOUT_SYNC``.
    """
    full = _prepare_argv(argv)
    data = stdin_text.encode("utf-8")  # UnicodeEncodeError (a ValueError) before spawn
    if not timeout or timeout <= 0:
        timeout = TIMEOUT_SYNC
    timed_out = threading.Event()
    with tempfile.TemporaryFile() as err_file:
        try:
            proc = subprocess.Popen(  # noqa: S603 — shell=False, arep only
                full,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=err_file,
                env=_child_env(),
                shell=False,
                start_new_session=True,
            )
        except OSError as exc:
            raise CliError(f"cannot start {full[0]}: {exc}", exit_code=1) from exc
        assert proc.stdout is not None

        def _on_timeout() -> None:
            timed_out.set()
            _kill_tree(proc)

        writer = threading.Thread(target=_feed_stdin, args=(proc, data), daemon=True)
        writer.start()
        timer = threading.Timer(timeout, _on_timeout)
        timer.start()
        try:
            _read_lines(proc.stdout, on_line)
            rc = proc.wait()
        except BaseException:
            _kill_tree(proc)
            proc.wait()
            raise
        finally:
            timer.cancel()
            writer.join(timeout=5.0)
        err_file.seek(0)
        stderr = err_file.read().decode("utf-8", errors="replace")
    return (124 if timed_out.is_set() else int(rc)), stderr


# --- entry point --------------------------------------------------------------------------


def run_rdma_transfer(
    *,
    node_id: str,
    direction: Direction,
    rels: Sequence[str],
    inv: Mapping[str, FileMeta],
    arep_bin: str = AREP_BIN,
    on_progress: ProgressCb,
    runner: XferRunner | None = None,
    timeout: float = TIMEOUT_SYNC,
) -> int:
    """Transfer *rels* via ``arep xfer <direction>``; return bytes moved.

    Raises ``TransportFailed("rdma", reason)`` on an ``error`` event, a
    non-zero exit, a timeout, or when arep cannot be started — the ladder
    then downgrades to the next rung. Failures after arep was spawned carry
    ``partial=True`` (bytes may have moved; the ladder re-stats), a failure
    to spawn does not. Empty *rels* is a no-op (0 bytes). A non-positive
    *timeout* falls back to ``TIMEOUT_SYNC`` — arep must never run unbounded.
    """
    if direction not in ("push", "pull"):
        raise ValueError(f"direction must be push|pull, got {direction!r}")
    node_id = _check_node_id(node_id)
    arep_bin = _check_arep_bin(arep_bin)
    if not timeout or timeout <= 0:
        timeout = TIMEOUT_SYNC
    rels = list(rels)
    if not rels:
        return 0
    manifest = manifest_text(rels, inv)  # KeyError here, before anything is spawned
    argv = [arep_bin, "xfer", direction, "--node", node_id, "--manifest", "-"]
    state = _XferState()
    run = runner or xfer_subprocess_runner
    try:
        rc, stderr = run(
            argv,
            stdin_text=manifest,
            on_line=lambda line: _handle_line(line, state, on_progress),
            timeout=timeout,
        )
    except TransportFailed:
        raise
    except CliError as exc:  # runner refused / could not start arep: nothing moved
        raise TransportFailed("rdma", f"arep xfer {direction}: {exc}") from exc
    except Exception as exc:  # reader/callback died mid-run: unknown how far arep got
        raise TransportFailed("rdma", f"arep xfer {direction}: {exc}", partial=True) from exc
    if state.error:
        raise TransportFailed("rdma", state.error, partial=True)
    if rc == 124:
        raise TransportFailed(
            "rdma", f"arep xfer {direction} timeout after {timeout:.0f}s", partial=True
        )
    if rc != 0:
        raw_tail = (stderr or "")[-4 * _STDERR_TAIL :]
        tail = clean_text(raw_tail, 4 * _STDERR_TAIL)[-_STDERR_TAIL:] or "no stderr"
        raise TransportFailed("rdma", f"arep exit {rc}: {tail}", partial=True)
    return state.done_bytes if state.done_bytes is not None else state.last_done
