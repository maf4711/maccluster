"""ProcessRunner — sole subprocess entry point (shell=False, allowlist, timeouts)."""

from __future__ import annotations

import os
import subprocess
import tempfile
import time
from collections.abc import Callable, Sequence
from pathlib import Path

from maccluster.adapters.process_stream import kill_process_group
from maccluster.constants import (
    ALLOWLIST_BASENAMES,
    EXTRA_SEARCH_PATHS,
    SEARCH_PATHS,
    TIMEOUT_GENERIC,
)
from maccluster.errors import CliError
from maccluster.ports.process import ProcessResult

ProgressChunkCb = Callable[[int, int], None]  # (bytes_done, bytes_total)


class ProcessRunner:
    """argv-only runner with basename allowlist and absolute path resolution."""

    def __init__(
        self,
        *,
        search_paths: Sequence[str] | None = None,
        extra_paths: Sequence[str] | None = None,
        allowlist: frozenset[str] | None = None,
    ) -> None:
        self._search = tuple(search_paths or SEARCH_PATHS)
        self._extra = tuple(extra_paths or EXTRA_SEARCH_PATHS)
        self._allowlist = allowlist or ALLOWLIST_BASENAMES

    def resolve(self, basename: str) -> str:
        if basename not in self._allowlist:
            raise CliError(
                f"refusing non-allowlisted binary: {basename!r}",
                exit_code=1,
            )
        paths = self._search
        if basename in ("iperf3", "ssh", "scp", "git", "gh", "bash", "arep"):
            paths = self._search + self._extra
        for directory in paths:
            candidate = Path(directory) / basename
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return str(candidate)
        raise CliError(f"tool not found: {basename}", exit_code=1)

    def _prepare_argv(self, argv: Sequence[str]) -> list[str]:
        if not argv:
            raise CliError("empty argv", exit_code=1)
        first = argv[0]
        if "/" in first:
            basename = Path(first).name
            if basename not in self._allowlist:
                raise CliError(
                    f"refusing non-allowlisted binary: {basename!r}",
                    exit_code=1,
                )
            abs_first = first
        else:
            abs_first = self.resolve(first)
        return [abs_first, *list(argv[1:])]

    def _child_env(self) -> dict[str, str]:
        env = {
            "PATH": "/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin:/usr/local/bin",
            "HOME": os.environ.get("HOME", ""),
            "USER": os.environ.get("USER", ""),
            "LANG": "C",
            "LC_ALL": "C",
        }
        for key in (
            "SSH_AUTH_SOCK",
            "SSH_CONNECTION",
            "SSH_TTY",
            "GIT_SSH_COMMAND",
            "GH_TOKEN",
            "GH_HOST",
            "GITHUB_TOKEN",
            "DEVELOPER_DIR",
        ):
            val = os.environ.get(key)
            if val:
                env[key] = val
        return env

    def run_pipe(
        self,
        producer: Sequence[str],
        consumer: Sequence[str],
        *,
        timeout: float = TIMEOUT_GENERIC,
    ) -> ProcessResult:
        """Run ``producer | consumer`` concurrently; return the consumer result.

        Staging an archive to disk, copying it, then unpacking it serialises
        three phases that can all run at once. Piping overlaps them, which is
        where the wall-clock win comes from.

        A producer that dies must not be masked by a consumer that cheerfully
        unpacked a truncated stream, so a non-zero producer status wins.
        Producer stderr goes to a temp file: reading it from a pipe while
        waiting on the consumer can deadlock once the buffer fills.
        """
        p_argv = self._prepare_argv(producer)
        c_argv = self._prepare_argv(consumer)
        env = self._child_env()
        argv_repr = (*p_argv, "|", *c_argv)
        deadline = time.monotonic() + timeout
        with tempfile.TemporaryFile() as perr:
            try:
                prod = subprocess.Popen(  # noqa: S603 — shell=False, allowlisted argv
                    p_argv,
                    stdout=subprocess.PIPE,
                    stderr=perr,
                    env=env,
                    shell=False,
                    start_new_session=True,
                )
            except OSError as exc:
                return ProcessResult(argv_repr, 127, "", str(exc), False)
            try:
                assert prod.stdout is not None
                cons = subprocess.Popen(  # noqa: S603 — shell=False, allowlisted argv
                    c_argv,
                    stdin=prod.stdout,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    env=env,
                    shell=False,
                    start_new_session=True,
                )
            except OSError as exc:
                kill_process_group(prod)
                prod.wait()
                return ProcessResult(argv_repr, 127, "", str(exc), False)
            # Close our copy so the producer sees EPIPE if the consumer exits.
            prod.stdout.close()
            timed_out = False
            try:
                out_b, err_b = cons.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                kill_process_group(cons)
                kill_process_group(prod)
                out_b, err_b = cons.communicate()
            try:
                prod.wait(timeout=max(0.0, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                timed_out = True
                kill_process_group(prod)
                prod.wait()
            perr.seek(0)
            p_err = perr.read().decode("utf-8", errors="replace")

        def _txt(blob: object) -> str:
            if blob is None:
                return ""
            if isinstance(blob, bytes):
                return blob.decode("utf-8", errors="replace")
            return str(blob)

        out, err = _txt(out_b), _txt(err_b)
        if timed_out:
            return ProcessResult(argv_repr, 124, out, (err + p_err).strip(), True)
        if prod.returncode:
            # Producer failure is the real cause even when the consumer exits 0.
            return ProcessResult(
                argv_repr,
                prod.returncode,
                out,
                (p_err or err or "pipe producer failed").strip(),
                False,
            )
        return ProcessResult(argv_repr, cons.returncode, out, (err + p_err).strip(), False)

    def run(
        self,
        argv: Sequence[str],
        *,
        timeout: float = TIMEOUT_GENERIC,
        check: bool = False,
    ) -> ProcessResult:
        full_argv = self._prepare_argv(argv)
        env = self._child_env()
        try:
            completed = subprocess.run(  # noqa: S603 — intentional; shell=False, allowlist
                full_argv,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                shell=False,
                env=env,
                check=False,
            )
            result = ProcessResult(
                argv=tuple(full_argv),
                returncode=completed.returncode,
                stdout=completed.stdout or "",
                stderr=completed.stderr or "",
                timed_out=False,
            )
        except subprocess.TimeoutExpired as exc:

            def _decode(blob: object) -> str:
                if blob is None:
                    return ""
                if isinstance(blob, bytes):
                    return blob.decode("utf-8", errors="replace")
                return str(blob)

            result = ProcessResult(
                argv=tuple(full_argv),
                returncode=124,
                stdout=_decode(exc.stdout),
                stderr=_decode(exc.stderr),
                timed_out=True,
            )
        if check and result.returncode != 0:
            raise CliError(
                f"command failed ({result.returncode}): {' '.join(full_argv)}: {result.stderr.strip()}",
                exit_code=1,
                details=result,
            )
        return result

    def stream_stdin_file(
        self,
        argv: Sequence[str],
        *,
        input_path: Path | str,
        timeout: float = TIMEOUT_GENERIC,
        chunk_size: int = 1024 * 1024,
        on_progress: ProgressChunkCb | None = None,
    ) -> ProcessResult:
        """Run command with file piped to stdin; optional byte progress callback."""
        from maccluster.adapters.process_stream import stream_file

        return stream_file(
            self._prepare_argv(argv),
            self._child_env(),
            path=Path(input_path),
            direction="stdin",
            timeout=timeout,
            expected_size=0,
            chunk_size=chunk_size,
            on_progress=on_progress,
        )

    def stream_stdout_file(
        self,
        argv: Sequence[str],
        *,
        output_path: Path | str,
        timeout: float = TIMEOUT_GENERIC,
        expected_size: int = 0,
        chunk_size: int = 1024 * 1024,
        on_progress: ProgressChunkCb | None = None,
    ) -> ProcessResult:
        """Run command and write stdout to file; optional byte progress callback."""
        from maccluster.adapters.process_stream import stream_file

        return stream_file(
            self._prepare_argv(argv),
            self._child_env(),
            path=Path(output_path),
            direction="stdout",
            timeout=timeout,
            expected_size=expected_size,
            chunk_size=chunk_size,
            on_progress=on_progress,
        )
