"""Cluster SSH helpers — force traffic onto the TB bridge (not Wi‑Fi).

All peer control-plane SSH/SCP for MacCluster MUST bind the local cluster Self-IP
and target peer IPs inside the configured subnet (default 10.42.0.0/24).
"""

from __future__ import annotations

import getpass
import os
import re
from ipaddress import IPv4Address, IPv4Network, ip_address, ip_network
from pathlib import Path

from maccluster.constants import DEFAULT_SUBNET
from maccluster.errors import CliError

# OpenSSH options that disable ControlMaster / agent oddities for cluster hops.
CLUSTER_SSH_BASE_OPTS: tuple[str, ...] = (
    "-o",
    "BatchMode=yes",
    "-o",
    "PasswordAuthentication=no",
    "-o",
    "KbdInteractiveAuthentication=no",
    "-o",
    "StrictHostKeyChecking=accept-new",
    "-o",
    "ControlMaster=no",
    "-o",
    "ControlPath=none",
    "-o",
    "IdentitiesOnly=yes",
)


def parse_subnet(text: str | IPv4Network | None = None) -> IPv4Network:
    if isinstance(text, IPv4Network):
        return text
    raw = (text or DEFAULT_SUBNET).strip()
    return ip_network(raw, strict=False)


def is_cluster_ip(ip: str | IPv4Address, subnet: IPv4Network | str | None = None) -> bool:
    net = parse_subnet(subnet if not isinstance(subnet, IPv4Network) else subnet)
    try:
        addr = ip_address(str(ip))
    except ValueError:
        return False
    return addr in net and addr.version == 4


def require_cluster_ip(ip: str | IPv4Address, subnet: IPv4Network | str | None = None) -> IPv4Address:
    net = parse_subnet(subnet if not isinstance(subnet, IPv4Network) else subnet)
    try:
        addr = IPv4Address(str(ip))
    except ValueError as exc:
        raise CliError(f"invalid IP: {ip!r}", exit_code=2) from exc
    if addr not in net:
        raise CliError(
            f"refusing non-cluster address {addr} (not in {net}). "
            f"MacCluster remote ops use the TB bridge only — not Wi‑Fi/LAN.",
            exit_code=2,
        )
    return addr


def ssh_user(default: str | None = None) -> str:
    u = (default or os.environ.get("USER") or getpass.getuser() or "").strip()
    if not u:
        raise CliError("cannot determine SSH username", exit_code=1)
    return u


def cluster_target(user: str, peer_ip: str | IPv4Address) -> str:
    return f"{user}@{IPv4Address(str(peer_ip))}"


def ssh_bind_argv(
    abs_ssh: str,
    *,
    bind_ip: str | IPv4Address,
    peer_ip: str | IPv4Address,
    user: str | None = None,
    connect_timeout: int = 8,
    identity_file: str | Path | None = None,
    remote: tuple[str, ...] = (),
) -> list[str]:
    """Build ssh argv bound to Self cluster IP → peer cluster IP."""
    bind = str(require_cluster_ip(bind_ip))
    peer = str(require_cluster_ip(peer_ip))
    target = cluster_target(ssh_user(user), peer)
    argv: list[str] = [
        abs_ssh,
        *CLUSTER_SSH_BASE_OPTS,
        "-o",
        f"ConnectTimeout={max(1, int(connect_timeout))}",
        "-o",
        f"BindAddress={bind}",
        "-b",
        bind,
    ]
    ident = identity_file or os.path.expanduser("~/.ssh/id_ed25519")
    if ident and Path(ident).is_file():
        argv.extend(["-i", str(ident)])
    argv.append(target)
    argv.extend(remote)
    return argv


def scp_bind_argv(
    abs_scp: str,
    *,
    bind_ip: str | IPv4Address,
    local_path: str | Path,
    peer_ip: str | IPv4Address,
    remote_path: str,
    user: str | None = None,
    connect_timeout: int = 8,
    identity_file: str | Path | None = None,
    to_remote: bool = True,
) -> list[str]:
    """Build scp argv with BindAddress to Self cluster IP."""
    bind = str(require_cluster_ip(bind_ip))
    peer = str(require_cluster_ip(peer_ip))
    target = cluster_target(ssh_user(user), peer)
    remote_spec = f"{target}:{remote_path}"
    argv: list[str] = [
        abs_scp,
        *CLUSTER_SSH_BASE_OPTS,
        "-o",
        f"ConnectTimeout={max(1, int(connect_timeout))}",
        "-o",
        f"BindAddress={bind}",
    ]
    # scp has no -b; OpenSSH scp honors BindAddress.
    ident = identity_file or os.path.expanduser("~/.ssh/id_ed25519")
    if ident and Path(ident).is_file():
        argv.extend(["-i", str(ident)])
    if to_remote:
        argv.extend([str(local_path), remote_spec])
    else:
        argv.extend([remote_spec, str(local_path)])
    return argv


def render_ssh_config_fragment(
    *,
    self_ip: str | IPv4Address,
    subnet: IPv4Network | str | None = None,
    user: str | None = None,
    identity_file: str = "~/.ssh/id_ed25519",
) -> str:
    """OpenSSH config: all 10.42.0.* (or subnet) bind to Self IP on bridge."""
    net = parse_subnet(subnet if not isinstance(subnet, IPv4Network) else subnet)
    bind = str(require_cluster_ip(self_ip, net))
    u = ssh_user(user)
    # Host pattern for /24: 10.42.0.*
    if net.prefixlen == 24:
        host_pat = str(net.network_address).rsplit(".", 1)[0] + ".*"
    else:
        host_pat = str(net.network_address) + "*"
    return f"""# MacCluster — TB bridge only (auto-generated; do not use Wi‑Fi for peers)
# Self bind: {bind}  subnet: {net}

Host {host_pat}
    HostName %h
    User {u}
    BindAddress {bind}
    IdentityFile {identity_file}
    IdentitiesOnly yes
    ControlMaster no
    ControlPath none
    StrictHostKeyChecking accept-new
    ServerAliveInterval 30
    ServerAliveCountMax 3
    # Prefer key only for automation-friendly peers
    PasswordAuthentication no

"""


def ensure_ssh_config_include(ssh_dir: Path | None = None) -> Path:
    """Ensure ~/.ssh/config Includes config.d/* and write maccluster fragment path."""
    ssh_dir = ssh_dir or Path.home() / ".ssh"
    ssh_dir.mkdir(mode=0o700, exist_ok=True)
    conf_d = ssh_dir / "config.d"
    conf_d.mkdir(mode=0o700, exist_ok=True)
    main = ssh_dir / "config"
    include_line = "Include config.d/*"
    if main.exists():
        text = main.read_text(encoding="utf-8")
        if not re.search(r"(?m)^\s*Include\s+config\.d/\*", text):
            main.write_text(include_line + "\n\n" + text, encoding="utf-8")
            main.chmod(0o600)
    else:
        main.write_text(include_line + "\n", encoding="utf-8")
        main.chmod(0o600)
    return conf_d / "maccluster"


def write_cluster_ssh_config(
    *,
    self_ip: str | IPv4Address,
    subnet: IPv4Network | str | None = None,
    user: str | None = None,
) -> Path:
    path = ensure_ssh_config_include()
    path.write_text(
        render_ssh_config_fragment(self_ip=self_ip, subnet=subnet, user=user),
        encoding="utf-8",
    )
    path.chmod(0o600)
    return path


def default_pubkey_path() -> Path:
    for name in ("id_ed25519.pub", "id_rsa.pub"):
        p = Path.home() / ".ssh" / name
        if p.is_file():
            return p
    raise CliError("no SSH public key found (~/.ssh/id_ed25519.pub)", exit_code=1)


def read_pubkey() -> str:
    return default_pubkey_path().read_text(encoding="utf-8").strip() + "\n"
