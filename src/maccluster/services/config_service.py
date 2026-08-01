"""Load and validate cluster config via AppContext."""

from __future__ import annotations

from pathlib import Path

from maccluster.app_factory import AppContext
from maccluster.config.load import load_toml_text
from maccluster.config.validate import assign_roles, validate_or_raise
from maccluster.constants import CONFIG_MAX_BYTES
from maccluster.domain.models import ClusterConfig, Node
from maccluster.errors import CliError, ConfigError


def load_config(ctx: AppContext, path: Path | None = None) -> ClusterConfig:
    cfg_path = path or ctx.config_path
    if not ctx.fs.exists(cfg_path):
        raise ConfigError(
            f"config not found: {cfg_path} — run `maccluster init`",
            details={"path": str(cfg_path)},
        )
    if ctx.fs.is_symlink(cfg_path):
        # Reading through symlink is ok for show; note only
        pass
    try:
        size = ctx.fs.size(cfg_path)
    except OSError as exc:
        raise CliError(f"cannot stat config: {cfg_path}: {exc}", exit_code=1) from exc
    if size > CONFIG_MAX_BYTES:
        raise ConfigError(f"config too large (>{CONFIG_MAX_BYTES} bytes): {cfg_path}")
    try:
        text = ctx.fs.read_text(cfg_path)
    except PermissionError as exc:
        raise CliError(f"permission denied reading config: {cfg_path}", exit_code=1) from exc
    except OSError as exc:
        raise CliError(f"cannot read config: {cfg_path}: {exc}", exit_code=1) from exc
    return load_toml_text(text)


def load_and_bind_self(ctx: AppContext) -> tuple[ClusterConfig, Node]:
    cfg = load_config(ctx)
    identity = ctx.identity.get_identity()
    return assign_roles(cfg, identity)


def validate_only(ctx: AppContext) -> tuple[ClusterConfig, Node]:
    cfg, self_node = load_and_bind_self(ctx)
    validate_or_raise(cfg)
    return cfg, self_node
