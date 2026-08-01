"""Initialize cluster.toml."""

from __future__ import annotations

from pathlib import Path

from maccluster.app_factory import AppContext
from maccluster.config.dump import dump_toml
from maccluster.config.init_template import build_init_config
from maccluster.errors import ConfigError


def init_cluster(
    ctx: AppContext,
    *,
    force: bool = False,
    name: str = "studio-cluster",
    node_count: int = 4,
    path: Path | None = None,
) -> Path:
    cfg_path = path or ctx.config_path
    if ctx.fs.exists(cfg_path) and not force:
        raise ConfigError(
            f"config already exists: {cfg_path} (use --force to overwrite with backup)"
        )
    if ctx.fs.is_symlink(cfg_path):
        raise ConfigError(f"refusing to write through symlink: {cfg_path}")

    identity = ctx.identity.get_identity()
    cfg = build_init_config(identity, name=name, node_count=node_count)
    text = dump_toml(cfg)
    ctx.fs.write_text_atomic(cfg_path, text, mode=0o600, backup=force and ctx.fs.exists(cfg_path))
    return cfg_path
