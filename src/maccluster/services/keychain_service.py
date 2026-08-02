"""MacCluster Keychain orchestration — shared cluster config + SSH user/password."""

from __future__ import annotations

import getpass
import os
from dataclasses import dataclass
from pathlib import Path

from maccluster.adapters.keychain_macos import KeychainStore
from maccluster.app_factory import AppContext
from maccluster.config.load import load_toml_text
from maccluster.constants import KEYCHAIN_ACCOUNT_DEFAULT
from maccluster.errors import CliError


@dataclass(frozen=True)
class KeychainSnapshot:
    account: str
    has_config: bool
    has_ssh_user: bool
    has_ssh_password: bool
    cluster_name: str | None
    ssh_user: str | None
    config_preview: str | None  # first lines, no secrets
    path_on_disk: str | None
    disk_exists: bool
    note: str


def _kc(ctx: AppContext) -> KeychainStore:
    return KeychainStore(ctx.runner)


def show_keychain(ctx: AppContext, *, account: str = KEYCHAIN_ACCOUNT_DEFAULT) -> KeychainSnapshot:
    kc = _kc(ctx)
    cfg_text = kc.get_cluster_config_toml(account=account)
    ssh_user = kc.get_ssh_user(account=account)
    has_pw = kc.get_ssh_password(account=account) is not None
    name = None
    preview = None
    if cfg_text:
        preview = "\n".join(cfg_text.strip().splitlines()[:12])
        try:
            cfg = load_toml_text(cfg_text)
            name = cfg.name
        except Exception:
            name = "(unparseable)"
    disk = ctx.config_path
    return KeychainSnapshot(
        account=account,
        has_config=bool(cfg_text),
        has_ssh_user=bool(ssh_user),
        has_ssh_password=has_pw,
        cluster_name=name,
        ssh_user=ssh_user,
        config_preview=preview,
        path_on_disk=str(disk),
        disk_exists=ctx.fs.exists(disk),
        note=(
            "login keychain; with iCloud Keychain + same Apple ID, peers can pull the same items"
        ),
    )


def push_config_to_keychain(
    ctx: AppContext,
    *,
    account: str = KEYCHAIN_ACCOUNT_DEFAULT,
    ssh_user: str | None = None,
    ssh_password: str | None = None,
    path: Path | None = None,
) -> KeychainSnapshot:
    """Write local cluster.toml (+ optional SSH user/password) into Keychain."""
    cfg_path = path or ctx.config_path
    if not ctx.fs.exists(cfg_path):
        raise CliError(
            f"no local config to push: {cfg_path} — run maccluster init first",
            exit_code=2,
        )
    text = ctx.fs.read_text(cfg_path)
    try:
        cfg = load_toml_text(text)
    except Exception as exc:
        raise CliError(f"invalid local config: {exc}", exit_code=2) from exc

    kc = _kc(ctx)
    kc.set_cluster_config_toml(text, account=account, cluster_name=cfg.name)

    user = ssh_user
    if user is None:
        # Prefer first peer ssh_target user
        for n in cfg.nodes:
            if n.ssh_target and "@" in n.ssh_target:
                user = n.ssh_target.split("@", 1)[0].strip()
                break
    if user is None:
        user = os.environ.get("USER") or getpass.getuser()
    if user:
        kc.set_ssh_user(user, account=account)
    if ssh_password is not None:
        from maccluster.constants import KEYCHAIN_SERVICE_SSH_PASSWORD

        if ssh_password == "":
            kc.delete_password(service=KEYCHAIN_SERVICE_SSH_PASSWORD, account=account)
        else:
            kc.set_ssh_password(ssh_password, account=account)

    return show_keychain(ctx, account=account)


def pull_config_from_keychain(
    ctx: AppContext,
    *,
    account: str = KEYCHAIN_ACCOUNT_DEFAULT,
    force: bool = False,
    path: Path | None = None,
) -> tuple[Path, KeychainSnapshot]:
    """Write Keychain cluster.toml to disk (init / peer bootstrap)."""
    kc = _kc(ctx)
    text = kc.get_cluster_config_toml(account=account)
    if not text:
        raise CliError(
            "no MacCluster config in Keychain — on primary Mac run: maccluster keychain push",
            exit_code=2,
        )
    # validate
    load_toml_text(text)
    cfg_path = path or ctx.config_path
    if ctx.fs.exists(cfg_path) and not force:
        raise CliError(
            f"config already exists: {cfg_path} (use --force to overwrite from Keychain)",
            exit_code=2,
        )
    if ctx.fs.is_symlink(cfg_path):
        raise CliError(f"refusing to write through symlink: {cfg_path}", exit_code=2)
    ctx.fs.write_text_atomic(cfg_path, text, mode=0o600, backup=force and ctx.fs.exists(cfg_path))
    return cfg_path, show_keychain(ctx, account=account)


def delete_keychain(ctx: AppContext, *, account: str = KEYCHAIN_ACCOUNT_DEFAULT) -> list[str]:
    return _kc(ctx).delete_all(account=account)


def resolve_ssh_user(
    ctx: AppContext,
    *,
    explicit: str | None = None,
    account: str = KEYCHAIN_ACCOUNT_DEFAULT,
) -> str:
    """CLI --user > Keychain > $USER."""
    if explicit and explicit.strip():
        return explicit.strip()
    try:
        u = _kc(ctx).get_ssh_user(account=account)
        if u and u.strip():
            return u.strip()
    except Exception:
        pass
    return (os.environ.get("USER") or getpass.getuser() or "mafoe").strip()


def format_keychain_snapshot(snap: KeychainSnapshot, *, show_preview: bool = True) -> str:
    lines = [
        f"keychain account={snap.account}",
        f"  config: {'yes' if snap.has_config else 'no'}"
        + (f"  cluster={snap.cluster_name}" if snap.cluster_name else ""),
        f"  ssh_user: {snap.ssh_user or '(none)'}",
        f"  ssh_password: {'set' if snap.has_ssh_password else 'not set'}",
        f"  disk: {snap.path_on_disk} exists={snap.disk_exists}",
        f"  note: {snap.note}",
    ]
    if show_preview and snap.config_preview:
        lines.append("  --- config preview ---")
        for row in snap.config_preview.splitlines():
            lines.append(f"  {row}")
    return "\n".join(lines)
