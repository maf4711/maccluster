"""Initialize cluster.toml — Keychain first, then template."""

from __future__ import annotations

from pathlib import Path

from maccluster.app_factory import AppContext
from maccluster.config.dump import dump_toml
from maccluster.config.init_template import build_init_config
from maccluster.config.load import load_toml_text
from maccluster.constants import KEYCHAIN_ACCOUNT_DEFAULT
from maccluster.errors import ConfigError
from maccluster.services.keychain_service import (
    pull_config_from_keychain,
    push_config_to_keychain,
    show_keychain,
)


def init_cluster(
    ctx: AppContext,
    *,
    force: bool = False,
    name: str = "studio-cluster",
    node_count: int = 4,
    path: Path | None = None,
    from_keychain: bool = True,
    save_keychain: bool = True,
    account: str = KEYCHAIN_ACCOUNT_DEFAULT,
) -> tuple[Path, str]:
    """
    Create or restore cluster.toml.

    Order:
      1) Peek Keychain (unless --no-keychain)
      2) If disk config exists and not --force → keep disk (do not error)
      3) If Keychain has config and disk missing (or --force) → pull to disk
      4) Else write template from local identity
      5) Optionally push result back to local login Keychain

    Returns (path, source) where source is 'disk' | 'keychain' | 'template'.
    """
    cfg_path = path or ctx.config_path

    # --- 1) Keychain peek ---
    snap = None
    if from_keychain:
        try:
            snap = show_keychain(ctx, account=account)
        except Exception:
            snap = None

    # --- 2) Disk already present: keep it unless --force ---
    if ctx.fs.exists(cfg_path) and not force:
        return cfg_path, "disk"

    # --- 3) Keychain restore when empty disk or forced ---
    if (
        from_keychain
        and snap is not None
        and snap.has_config
        and snap.cluster_name not in (None, "(unparseable)")
    ):
        try:
            out, _ = pull_config_from_keychain(
                ctx,
                account=account,
                force=True,  # we already decided overwrite or empty
                path=cfg_path,
            )
            return out, "keychain"
        except ConfigError:
            # Corrupt keychain → fall through to template
            pass
        except Exception:
            pass

    if ctx.fs.exists(cfg_path) and not force:
        # Defensive (should have returned 'disk' above)
        raise ConfigError(
            f"config already exists: {cfg_path} (use --force to overwrite with backup)"
        )
    if ctx.fs.is_symlink(cfg_path):
        raise ConfigError(f"refusing to write through symlink: {cfg_path}")

    identity = ctx.identity.get_identity()
    cfg = build_init_config(identity, name=name, node_count=node_count)
    text = dump_toml(cfg)
    # If keychain had ssh user, annotate first peer after write via reload+optional
    ctx.fs.write_text_atomic(cfg_path, text, mode=0o600, backup=force and ctx.fs.exists(cfg_path))

    if save_keychain:
        try:
            push_config_to_keychain(ctx, account=account, path=cfg_path)
        except Exception:
            # Non-fatal: disk config is source of truth if keychain locked
            pass

    return cfg_path, "template"


def apply_keychain_ssh_targets(
    ctx: AppContext,
    *,
    account: str = KEYCHAIN_ACCOUNT_DEFAULT,
    path: Path | None = None,
) -> Path | None:
    """If Keychain has ssh_user, set ssh_target=user@ip on all non-self peers and rewrite TOML."""
    from maccluster.services.config_service import load_and_bind_self
    from maccluster.services.keychain_service import resolve_ssh_user

    cfg_path = path or ctx.config_path
    if not ctx.fs.exists(cfg_path):
        return None
    try:
        cfg, self_node = load_and_bind_self(ctx)
    except Exception:
        text = ctx.fs.read_text(cfg_path)
        cfg = load_toml_text(text)
        self_node = None

    user = resolve_ssh_user(ctx, account=account)
    changed = False
    new_nodes = []
    for n in cfg.nodes:
        if self_node is not None and n.id == self_node.id:
            new_nodes.append(n)
            continue
        desired = f"{user}@{n.ip}"
        if n.ssh_target != desired:
            from maccluster.domain.models import Node

            new_nodes.append(
                Node(
                    id=n.id,
                    hostnames=n.hostnames,
                    ip=n.ip,
                    hw_uuid=n.hw_uuid,
                    ssh_target=desired,
                    role=n.role,
                )
            )
            changed = True
        else:
            new_nodes.append(n)
    if not changed:
        return cfg_path

    from dataclasses import replace

    # ClusterConfig is frozen
    new_cfg = replace(cfg, nodes=tuple(new_nodes))
    ctx.fs.write_text_atomic(cfg_path, dump_toml(new_cfg), mode=0o600, backup=True)
    try:
        push_config_to_keychain(ctx, account=account, path=cfg_path, ssh_user=user)
    except Exception:
        pass
    return cfg_path
