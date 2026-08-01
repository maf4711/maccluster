"""ProcessRunner — sole subprocess entry point (shell=False, allowlist, timeouts)."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Sequence
from pathlib import Path

from maccluster.constants import (
    ALLOWLIST_BASENAMES,
    EXTRA_SEARCH_PATHS,
    SEARCH_PATHS,
    TIMEOUT_GENERIC,
)
from maccluster.errors import CliError
from maccluster.ports.process import ProcessResult


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
        if basename in ("iperf3", "ssh"):
            paths = self._search + self._extra
        for directory in paths:
            candidate = Path(directory) / basename
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return str(candidate)
        raise CliError(f"tool not found: {basename}", exit_code=1)

    def run(
        self,
        argv: Sequence[str],
        *,
        timeout: float = TIMEOUT_GENERIC,
        check: bool = False,
    ) -> ProcessResult:
        if not argv:
            raise CliError("empty argv", exit_code=1)
        first = argv[0]
        # Absolute path or basename
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
        full_argv = [abs_first, *list(argv[1:])]
        env = {
            "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
            "HOME": os.environ.get("HOME", ""),
            "USER": os.environ.get("USER", ""),
            "LANG": "C",
            "LC_ALL": "C",
        }
        try:
            completed = subprocess.run(  # noqa: S603 — intentional; shell=False, allowlist
                full_argv,
                capture_output=True,
                text=True,
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
            result = ProcessResult(
                argv=tuple(full_argv),
                returncode=124,
                stdout=(exc.stdout or b"").decode()
                if isinstance(exc.stdout, bytes)
                else (exc.stdout or ""),
                stderr=(exc.stderr or b"").decode()
                if isinstance(exc.stderr, bytes)
                else (exc.stderr or ""),
                timed_out=True,
            )
        if check and result.returncode != 0:
            raise CliError(
                f"command failed ({result.returncode}): {' '.join(full_argv)}: {result.stderr.strip()}",
                exit_code=1,
                details=result,
            )
        return result
