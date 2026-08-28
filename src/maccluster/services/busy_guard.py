"""Saturation guard: skip fabric-filling benches when the operator marks busy."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from maccluster.config.paths import default_config_dir
from maccluster.errors import CliError
from maccluster.render.sanitize import sanitize

BUSY_ENV = "MACCLUSTER_BUSY"
BUSY_FILE_NAME = "busy"
_TRUTHY = frozenset({"1", "true", "yes", "on"})
_FALSY = frozenset({"0", "false", "no", "off", ""})


@dataclass(frozen=True)
class BusyState:
    busy: bool
    reason: str  # empty when idle


def default_busy_path() -> Path:
    return default_config_dir() / BUSY_FILE_NAME


def read_busy_state(
    *,
    env: Mapping[str, str] | None = None,
    busy_path: Path | None = None,
) -> BusyState:
    """Env wins; then the busy file. Symlink path → Exit 2."""
    environ = env if env is not None else os.environ
    raw = str(environ.get(BUSY_ENV, "")).strip()
    if raw:
        key = raw.lower()
        if key in _TRUTHY:
            return BusyState(True, f"{BUSY_ENV}={raw}")
        if key in _FALSY:
            pass
        else:
            return BusyState(True, f"{BUSY_ENV}={raw}")

    path = busy_path if busy_path is not None else default_busy_path()
    if path.is_symlink():
        raise CliError(f"refusing busy file symlink: {path}", exit_code=2)
    if not path.is_file():
        return BusyState(False, "")
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise CliError(f"cannot read busy file: {path}: {exc}", exit_code=1) from exc
    first = ""
    for line in text.splitlines():
        first = line.strip()
        if first:
            break
    reason = sanitize(first, max_len=120) if first else "busy file present"
    return BusyState(True, reason)
