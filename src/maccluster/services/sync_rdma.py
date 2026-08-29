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
import os
import subprocess
import tempfile
import threading
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Protocol

from maccluster.constants import TIMEOUT_SYNC
from maccluster.errors import CliError
from maccluster.services.transport_ladder import AREP_BIN, TransportFailed, arep_process_runner

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


def manifest_lines(rels: Iterable[str], inv: Mapping[str, FileMeta]) -> Iterator[str]:
    """JSON-Lines rows ``{"rel","size","mtimeNs"}`` (no newline) in plan order.

    Every *rel* must be in *inv* (``KeyError`` otherwise) — a silently dropped
    file would look like a successful sync.
    """
    for rel in rels:
        meta = inv[rel]
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
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return int(value)


def _handle_line(line: str, state: _XferState, on_progress: ProgressCb) -> None:
    text = line.strip()
    if not text.startswith("{"):
        return
    try:
        event = json.loads(text)
    except ValueError:
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
        state.error = str(event.get("reason") or "arep reported error")


# --- default subprocess runner ----------------------------------------------------------------


def _prepare_argv(argv: Sequence[str]) -> list[str]:
    if not argv:
        raise CliError("empty argv", exit_code=1)
    first = argv[0]
    if "/" in first:
        if Path(first).name != AREP_BIN:
            raise CliError(f"refusing non-{AREP_BIN} binary: {first!r}", exit_code=1)
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


def _feed_stdin(proc: subprocess.Popen, text: str) -> None:
    assert proc.stdin is not None
    try:
        proc.stdin.write(text.encode("utf-8"))
    except (BrokenPipeError, OSError):
        pass
    finally:
        try:
            proc.stdin.close()
        except OSError:
            pass


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
    A kill on *timeout* reports rc 124 like ``ProcessRunner``.
    """
    full = _prepare_argv(argv)
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
            )
        except OSError as exc:
            raise CliError(f"cannot start {full[0]}: {exc}", exit_code=1) from exc
        assert proc.stdout is not None

        def _kill() -> None:
            timed_out.set()
            proc.kill()

        writer = threading.Thread(target=_feed_stdin, args=(proc, stdin_text), daemon=True)
        writer.start()
        timer = threading.Timer(timeout, _kill) if timeout and timeout > 0 else None
        if timer:
            timer.start()
        try:
            for raw in proc.stdout:
                on_line(raw.decode("utf-8", errors="replace").rstrip("\r\n"))
            rc = proc.wait()
        finally:
            if timer:
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
    then downgrades to the next rung. Empty *rels* is a no-op (0 bytes).
    """
    if direction not in ("push", "pull"):
        raise ValueError(f"direction must be push|pull, got {direction!r}")
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
    except Exception as exc:
        raise TransportFailed("rdma", f"arep xfer {direction}: {exc}") from exc
    if state.error:
        raise TransportFailed("rdma", state.error)
    if rc == 124:
        raise TransportFailed("rdma", f"arep xfer {direction} timeout after {timeout:.0f}s")
    if rc != 0:
        tail = " ".join(stderr.split())[-_STDERR_TAIL:] or "no stderr"
        raise TransportFailed("rdma", f"arep exit {rc}: {tail}")
    return state.done_bytes if state.done_bytes is not None else state.last_done
