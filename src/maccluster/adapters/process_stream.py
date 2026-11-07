"""File-backed subprocess streams with bounded waits and progress reporting."""

from __future__ import annotations

import os
import signal
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import Literal

from maccluster.errors import CliError
from maccluster.ports.process import ProcessResult


def kill_process_group(proc: subprocess.Popen) -> None:
    """Stop children too: inherited pipes must not outlive the deadline."""
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def stream_file(
    argv: list[str],
    env: dict[str, str],
    *,
    path: Path,
    direction: Literal["stdin", "stdout"],
    timeout: float,
    expected_size: int,
    chunk_size: int,
    on_progress: Callable[[int, int], None] | None,
) -> ProcessResult:
    if chunk_size <= 0:
        raise CliError("chunk_size must be positive", exit_code=2)
    sending = direction == "stdin"
    if not sending:
        path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout if timeout else None
    try:
        with path.open("rb" if sending else "wb") as fh:
            total = path.stat().st_size if sending else expected_size
            with subprocess.Popen(
                argv,
                stdin=fh if sending else subprocess.DEVNULL,
                stdout=subprocess.PIPE if sending else fh,
                stderr=subprocess.PIPE,
                env=env,
                shell=False,
                start_new_session=True,
            ) as proc:
                timed_out = False
                try:
                    while True:
                        remaining = None if deadline is None else deadline - time.monotonic()
                        if remaining is not None and remaining <= 0:
                            timed_out = True
                            kill_process_group(proc)
                            out, err = proc.communicate()
                            break
                        wait = remaining
                        if on_progress:
                            wait = min(0.1, remaining) if remaining is not None else 0.1
                        try:
                            out, err = proc.communicate(timeout=wait)
                            break
                        except subprocess.TimeoutExpired:
                            if on_progress:
                                done = fh.tell()
                                on_progress(done, total if total > 0 else done)
                    if on_progress and not timed_out:
                        done = fh.tell()
                        on_progress(done, total if total > 0 else done)
                finally:
                    if proc.poll() is None:
                        kill_process_group(proc)
                    proc.wait()
                return ProcessResult(
                    tuple(argv),
                    124 if timed_out else proc.returncode,
                    (out or b"").decode("utf-8", errors="replace"),
                    (err or b"").decode("utf-8", errors="replace"),
                    timed_out,
                )
    except (OSError, ValueError) as exc:
        raise CliError(f"stream_{direction}_file failed: {exc}", exit_code=1) from exc
