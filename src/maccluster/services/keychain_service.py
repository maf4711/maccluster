"""MacCluster Keychain orchestration — local cluster config + SSH user/password.

The login Keychain is **per-Mac**. Peers get config via ``push-peer`` (TB SSH)
or ``remote-install``, never via iCloud Keychain (``security`` cannot create
synchronizable items).
"""

from __future__ import annotations

import getpass
import os
import shlex
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


@dataclass(frozen=True)
class PushPeerResult:
    peer_id: str
    peer_ip: str
    bind_ip: str
    ssh_target: str
    config_planted: bool
    keychain_pushed: bool
    message: str
    log: str = ""


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
            "local login keychain (this Mac only) — `security` cannot create "
            "iCloud-synchronizable items; use `maccluster keychain push-peer` "
            "or `remote-install` for peers"
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


def push_config_to_peer(
    ctx: AppContext,
    peer: str,
    *,
    account: str = KEYCHAIN_ACCOUNT_DEFAULT,
    user: str | None = None,
    force: bool = False,
    plant_keychain: bool = True,
    timeout: float = 60.0,
) -> PushPeerResult:
    """Copy cluster.toml to a peer over the TB bridge; optionally run keychain push there.

    Keychain items never cross Macs via iCloud. This is the supported peer path:
    SCP the config file, then try ``maccluster keychain push`` on the peer.
    Remote Keychain writes need an unlocked login keychain (local GUI session or
    ``security unlock-keychain``); file plant still succeeds if Keychain is locked.
    """
    from maccluster.cluster_ssh import (
        cluster_target,
        is_cluster_ip,
        node_ssh_user,
        require_cluster_ip,
        scp_bind_argv,
        ssh_bind_argv,
        write_cluster_ssh_config,
    )
    from maccluster.services.config_service import load_and_bind_self

    cfg_path = ctx.config_path
    if not ctx.fs.exists(cfg_path):
        # Prefer Keychain as source if disk missing
        text = _kc(ctx).get_cluster_config_toml(account=account)
        if not text:
            raise CliError(
                f"no local config at {cfg_path} and nothing in Keychain — "
                "run maccluster init / keychain push first",
                exit_code=2,
            )
        load_toml_text(text)
        cfg_path.parent.mkdir(parents=True, exist_ok=True)
        ctx.fs.write_text_atomic(cfg_path, text, mode=0o600, backup=False)

    cfg, self_node = load_and_bind_self(ctx)
    subnet = cfg.subnet
    self_ip = str(self_node.ip)
    require_cluster_ip(self_ip, subnet)

    peer_node = None
    for n in cfg.nodes:
        if n.id == self_node.id:
            continue
        if peer in (n.id, str(n.ip)):
            peer_node = n
            break
    if peer_node is None:
        if is_cluster_ip(peer, subnet):
            from maccluster.domain.enums import NodeRole
            from maccluster.domain.models import Node

            peer_node = Node(
                id=f"ip-{peer}",
                hostnames=(),
                ip=require_cluster_ip(peer, subnet),
                hw_uuid="",
                role=NodeRole.PEER,
            )
        else:
            raise CliError(
                f"peer {peer!r} not in cluster.toml and not a cluster IP in {subnet}",
                exit_code=2,
            )

    peer_ip = str(require_cluster_ip(peer_node.ip, subnet))
    # Prefer CLI --user, then node ssh_target, then Keychain ssh user, then $USER
    u = (user or "").strip() or None
    if not u:
        tgt = getattr(peer_node, "ssh_target", None) or ""
        if "@" in tgt:
            u = tgt.split("@", 1)[0].strip() or None
    if not u:
        try:
            ku = _kc(ctx).get_ssh_user(account=account)
            if ku and ku.strip():
                u = ku.strip()
        except Exception:
            pass
    if not u:
        u = node_ssh_user(peer_node, override=None)
    target = cluster_target(u, peer_ip)

    try:
        write_cluster_ssh_config(self_ip=self_ip, subnet=subnet, user=u)
    except Exception:
        pass

    abs_ssh = ctx.runner.resolve("ssh")
    abs_scp = ctx.runner.resolve("scp")
    logs: list[str] = []

    # Preflight
    probe = ctx.runner.run(
        ssh_bind_argv(
            abs_ssh,
            bind_ip=self_ip,
            peer_ip=peer_ip,
            user=u,
            connect_timeout=8,
            remote=("/usr/bin/true",),
        ),
        timeout=15.0,
        check=False,
    )
    if probe.returncode != 0:
        detail = (probe.stderr or probe.stdout or "").strip()
        raise CliError(
            f"SSH preflight to {target} failed (rc={probe.returncode}): {detail}",
            exit_code=1,
        )

    # One remote argv only — OpenSSH joins multiple args with spaces and breaks bash -lc
    def _ssh_one(remote_cmd: str, *, timeout_s: float = 15.0):
        return ctx.runner.run(
            ssh_bind_argv(
                abs_ssh,
                bind_ip=self_ip,
                peer_ip=peer_ip,
                user=u,
                connect_timeout=8,
                remote=(remote_cmd,),
            ),
            timeout=timeout_s,
            check=False,
        )

    # Optional refuse-if-exists (shell expands $HOME)
    if not force:
        check = _ssh_one('test ! -f "$HOME/.config/maccluster/cluster.toml"')
        if check.returncode != 0:
            raise CliError(
                f"peer already has cluster.toml (use --force to overwrite): {target}",
                exit_code=2,
            )

    mkdir = _ssh_one(
        '/bin/mkdir -p "$HOME/.config/maccluster" && /bin/chmod 700 "$HOME/.config/maccluster"'
    )
    if mkdir.returncode != 0:
        raise CliError(
            f"cannot create config dir on peer: {(mkdir.stderr or mkdir.stdout or '').strip()}",
            exit_code=1,
        )

    # scp needs a concrete remote path (no $HOME). Expand via remote printenv.
    home_r = _ssh_one('printf %s "$HOME"')
    if home_r.returncode != 0 or not (home_r.stdout or "").strip():
        raise CliError("cannot resolve remote $HOME", exit_code=1)
    remote_home = (home_r.stdout or "").strip()
    remote_abs = f"{remote_home}/.config/maccluster/cluster.toml"

    scp = ctx.runner.run(
        scp_bind_argv(
            abs_scp,
            bind_ip=self_ip,
            local_path=cfg_path,
            peer_ip=peer_ip,
            remote_path=remote_abs,
            user=u,
            connect_timeout=8,
            to_remote=True,
        ),
        timeout=max(30.0, timeout),
        check=False,
    )
    if scp.returncode != 0:
        raise CliError(
            f"scp to peer failed: {(scp.stderr or scp.stdout or '').strip()}",
            exit_code=1,
        )
    logs.append(f"planted {remote_abs}")

    # chmod
    _ssh_one(f"/bin/chmod 600 {shlex.quote(remote_abs)}")

    keychain_ok = False
    msg_parts = [f"config planted on {target}:{remote_abs}"]
    if plant_keychain:
        # Prefer installed maccluster on peer PATH
        remote_push = (
            'export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"; '
            "if command -v maccluster >/dev/null 2>&1; then "
            f"maccluster keychain push --account {shlex.quote(account)}; "
            "else "
            "echo 'maccluster not on PATH — skip keychain push "
            "(install first: maccluster remote-install)'; "
            "exit 3; "
            "fi"
        )
        kc_r = _ssh_one(remote_push, timeout_s=max(30.0, timeout))
        out = ((kc_r.stdout or "") + "\n" + (kc_r.stderr or "")).strip()
        logs.append(out or f"keychain push rc={kc_r.returncode}")
        if kc_r.returncode == 0:
            keychain_ok = True
            msg_parts.append("peer Keychain updated")
        elif kc_r.returncode == 3:
            msg_parts.append(
                "peer has no maccluster CLI — config file only; "
                "run remote-install, then on peer: maccluster keychain push"
            )
        else:
            msg_parts.append(
                "peer Keychain write failed (login keychain often locked over SSH) — "
                "config file is on disk; on peer GUI session run: "
                "maccluster keychain push"
            )
    else:
        msg_parts.append("skipped remote keychain (--no-keychain)")

    return PushPeerResult(
        peer_id=peer_node.id,
        peer_ip=peer_ip,
        bind_ip=self_ip,
        ssh_target=target,
        config_planted=True,
        keychain_pushed=keychain_ok,
        message="; ".join(msg_parts),
        log="\n".join(logs),
    )


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
