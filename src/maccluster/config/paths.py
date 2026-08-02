"""Config path resolution (AD-6): --config > MACCLUSTER_CONFIG > default."""

from __future__ import annotations

import os
from pathlib import Path

from maccluster.constants import CONFIG_DIR_NAME, CONFIG_FILE_NAME, LOCK_FILE_NAME


def default_config_dir() -> Path:
    return Path.home() / ".config" / CONFIG_DIR_NAME


def default_config_path() -> Path:
    return default_config_dir() / CONFIG_FILE_NAME


def default_lock_path() -> Path:
    return default_config_dir() / LOCK_FILE_NAME


def resolve_config_path(
    cli_path: str | Path | None = None,
    env: dict[str, str] | None = None,
) -> Path:
    """Resolve cluster.toml path. CLI wins over env over default."""
    if cli_path is not None and str(cli_path).strip():
        return Path(cli_path).expanduser().resolve()
    environ = env if env is not None else os.environ
    env_path = environ.get("MACCLUSTER_CONFIG", "").strip()
    if env_path:
        return Path(env_path).expanduser().resolve()
    return default_config_path()


def default_audit_log_path() -> Path:
    return Path.home() / ".local" / "state" / CONFIG_DIR_NAME / "actions.log"


def default_sync_exclude_file() -> Path:
    return default_config_dir() / "sync-excludes"


def default_sync_log_dir() -> Path:
    return Path.home() / "Library" / "Logs" / CONFIG_DIR_NAME


def default_sync_state_path() -> Path:
    return Path.home() / "Library" / "Caches" / CONFIG_DIR_NAME / "sync_state.json"


def default_safetynet_root() -> Path:
    return Path.home() / ".maccluster-safetynet"
