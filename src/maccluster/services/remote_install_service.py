"""Install MacCluster on a peer over the TB bridge only (SSH + local wheel)."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from maccluster.app_factory import AppContext
from maccluster.cluster_ssh import (
    cluster_target,
    is_cluster_ip,
    node_ssh_user,
    read_pubkey,
    require_cluster_ip,
    scp_bind_argv,
    ssh_bind_argv,
    write_cluster_ssh_config,
)
from maccluster.errors import CliError
from maccluster.services.config_service import load_and_bind_self

REMOTE_INSTALL_SH = r"""
set -euo pipefail
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
WHEEL="$1"
CFG="$2"
PUBKEY="$3"

echo "==> args wheel=$WHEEL cfg=$CFG pubkey=$PUBKEY"
test -f "$WHEEL" || { echo "missing wheel"; exit 1; }
test -f "$CFG" || { echo "missing config"; exit 1; }
test -f "$PUBKEY" || { echo "missing pubkey"; exit 1; }

echo "==> ensure pipx"
if ! command -v pipx >/dev/null 2>&1; then
  if command -v brew >/dev/null 2>&1; then
    brew install pipx
    pipx ensurepath || true
  else
    python3 -m pip install --user pipx
    python3 -m pipx ensurepath || true
  fi
  export PATH="$HOME/.local/bin:$PATH"
fi

echo "==> pipx install $WHEEL"
pipx install --force "$WHEEL"
export PATH="$HOME/.local/bin:$PATH"
hash -r || true
maccluster --version

echo "==> plant SSH pubkey for cluster remote-install"
mkdir -p "$HOME/.ssh"
/bin/chmod 700 "$HOME/.ssh"
touch "$HOME/.ssh/authorized_keys"
/bin/chmod 600 "$HOME/.ssh/authorized_keys"
if ! /usr/bin/grep -qF "$(/usr/bin/awk '{print $2}' "$PUBKEY")" "$HOME/.ssh/authorized_keys" 2>/dev/null; then
  /bin/cat "$PUBKEY" >> "$HOME/.ssh/authorized_keys"
  echo "authorized_keys updated"
else
  echo "pubkey already present"
fi

echo "==> cluster config"
mkdir -p "$HOME/.config/maccluster"
/bin/cp "$CFG" "$HOME/.config/maccluster/cluster.toml"
/bin/chmod 600 "$HOME/.config/maccluster/cluster.toml"
maccluster config validate

echo "==> bridge up (TB only) + heal service"
# Never hang on interactive sudo — passwordless only
if sudo -n true 2>/dev/null; then
  sudo -n maccluster up || echo "warn: maccluster up failed"
else
  echo "warn: no passwordless sudo — run on peer: sudo maccluster up"
fi
maccluster service install || true
maccluster doctor || true
maccluster status || true
echo "remote install complete on $(hostname)"
"""


@dataclass(frozen=True)
class RemoteInstallResult:
    peer_id: str
    peer_ip: str
    bind_ip: str
    ssh_target: str
    wheel: str
    ok: bool
    message: str
    log: str = ""


def resolve_install_user(user: str | None, peer_node) -> str:
    """SSH user for the peer: explicit --user, else user from the node's
    ssh_target in cluster.toml, else local $USER."""
    return node_ssh_user(peer_node, override=user)


def _project_root_candidates() -> list[Path]:
    out: list[Path] = []
    env = os.environ.get("MACCLUSTER_SRC")
    if env:
        out.append(Path(env).expanduser())
    # common dev paths
    home = Path.home()
    out.extend(
        [
            home / "Developer" / "fabrik" / "projects" / "maccluster",
            home / "Developer" / "maccluster",
            Path(__file__).resolve().parents[3],  # src/maccluster/services -> repo?
        ]
    )
    # parents[2] = src, parents[3] = project root when installed editable from checkout
    try:
        pkg = Path(__file__).resolve()
        # .../maccluster/src/maccluster/services/this.py -> root is parents[3]
        cand = pkg.parents[3]
        out.append(cand)
        out.append(pkg.parents[2])
    except Exception:
        pass
    return out


def find_or_build_wheel(*, work: Path) -> Path:
    env_w = os.environ.get("MACCLUSTER_WHEEL")
    if env_w and Path(env_w).is_file():
        return Path(env_w)

    cache = Path.home() / "Library" / "Caches" / "maccluster" / "wheels"
    cache.mkdir(parents=True, exist_ok=True)
    existing = sorted(cache.glob("maccluster-*.whl"), key=lambda p: p.stat().st_mtime, reverse=True)
    # Prefer rebuild from source if checkout present
    root = None
    for cand in _project_root_candidates():
        if (cand / "pyproject.toml").is_file() and (cand / "src" / "maccluster").is_dir():
            root = cand
            break
    if root is not None:
        wheel_dir = work / "wheels"
        wheel_dir.mkdir(parents=True, exist_ok=True)
        cmd = [
            "python3",
            "-m",
            "pip",
            "wheel",
            "-w",
            str(wheel_dir),
            "--no-deps",
            str(root),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if proc.returncode != 0:
            raise CliError(
                f"cannot build wheel: {proc.stderr or proc.stdout}",
                exit_code=1,
            )
        built = sorted(wheel_dir.glob("maccluster-*.whl"))
        if not built:
            raise CliError("pip wheel produced no maccluster-*.whl", exit_code=1)
        dest = cache / built[-1].name
        shutil.copy2(built[-1], dest)
        return dest

    if existing:
        return existing[0]
    raise CliError(
        "no maccluster wheel and no source checkout to build. "
        "Set MACCLUSTER_SRC or MACCLUSTER_WHEEL, or run from the maccluster repo.",
        exit_code=1,
    )


def remote_install(
    ctx: AppContext,
    peer: str,
    *,
    user: str | None = None,
    copy_config: bool = True,
    dry_run: bool = False,
    setup_ssh_config: bool = True,
    timeout: float = 600.0,
) -> RemoteInstallResult:
    """Install current MacCluster onto peer via TB bridge SSH only."""
    cfg, self_node = load_and_bind_self(ctx)
    subnet = cfg.subnet
    self_ip = str(self_node.ip)
    require_cluster_ip(self_ip, subnet)

    # resolve peer
    peer_node = None
    for n in cfg.nodes:
        if n.id == self_node.id:
            continue
        if peer in (n.id, str(n.ip)):
            peer_node = n
            break
    if peer_node is None:
        # raw IP in cluster subnet
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
    u = resolve_install_user(user, peer_node)
    target = cluster_target(u, peer_ip)

    if setup_ssh_config:
        write_cluster_ssh_config(self_ip=self_ip, subnet=subnet, user=u)

    # Startup cable + speed check (TB path grade; iperf if peer SSH allows)
    try:
        from maccluster.services.speedtest_service import (
            format_speedtest_report,
            run_speedtest,
        )

        st_peer = peer_node.id if not str(peer_node.id).startswith("ip-") else peer_ip
        st = run_speedtest(
            ctx,
            peer=st_peer,
            duration=3,
            skip_iperf=False,
            try_start_server=True,
        )
        # Always print cable grade before install (caller can log)
        print(format_speedtest_report(st), flush=True)
        if not st.good_enough:
            print(
                "warning: TB cable path below ideal (want ≥20–40 Gb/s). "
                "Install continues; fix cable for full mesh performance.",
                flush=True,
            )
    except Exception as exc:
        print(f"warning: speedtest preflight skipped: {exc}", flush=True)

    abs_ssh = ctx.runner.resolve("ssh")
    abs_scp = ctx.runner.resolve("scp")

    # Preflight on bridge only
    probe = ctx.runner.run(
        ssh_bind_argv(
            abs_ssh,
            bind_ip=self_ip,
            peer_ip=peer_ip,
            user=u,
            connect_timeout=8,
            remote=("/usr/bin/true",),
        ),
        timeout=12.0,
    )
    if probe.returncode != 0 and not dry_run:
        detail = (probe.stderr or probe.stdout or "").strip()[:300]
        raise CliError(
            f"SSH over TB bridge failed ({self_ip} → {peer_ip}). "
            f"Peer must have bridge IP + authorized_keys. "
            f"Bootstrap once via AirDrop (maccluster-peer-install.zip). detail: {detail}",
            exit_code=1,
        )

    with tempfile.TemporaryDirectory(prefix="maccluster-remote-") as tmp:
        work = Path(tmp)
        wheel = find_or_build_wheel(work=work)
        cfg_path = ctx.config_path
        if copy_config and not Path(cfg_path).is_file():
            raise CliError(f"config missing: {cfg_path}", exit_code=2)
        pubkey = read_pubkey()
        pub_file = work / "id_cluster.pub"
        pub_file.write_text(pubkey, encoding="utf-8")
        script = work / "remote_install_peer.sh"
        script.write_text(REMOTE_INSTALL_SH.lstrip(), encoding="utf-8")

        if dry_run:
            return RemoteInstallResult(
                peer_id=peer_node.id,
                peer_ip=peer_ip,
                bind_ip=self_ip,
                ssh_target=target,
                wheel=str(wheel),
                ok=True,
                message=f"dry-run: would install {wheel.name} on {target} via BindAddress {self_ip}",
            )

        remote_dir = f"/tmp/maccluster-install-{int(time.time())}"
        # Single remote argv — OpenSSH joins multiple args with spaces (breaks bash -lc)
        ctx.runner.run(
            ssh_bind_argv(
                abs_ssh,
                bind_ip=self_ip,
                peer_ip=peer_ip,
                user=u,
                remote=(f"/bin/mkdir -p {remote_dir}",),
            ),
            timeout=30.0,
        )

        files = {
            wheel: f"{remote_dir}/{wheel.name}",
            Path(cfg_path): f"{remote_dir}/cluster.toml",
            pub_file: f"{remote_dir}/cluster.pub",
            script: f"{remote_dir}/install.sh",
        }
        for local, remote in files.items():
            r = ctx.runner.run(
                scp_bind_argv(
                    abs_scp,
                    bind_ip=self_ip,
                    local_path=local,
                    peer_ip=peer_ip,
                    remote_path=remote,
                    user=u,
                    connect_timeout=15,
                ),
                timeout=timeout,
            )
            if r.returncode != 0:
                raise CliError(
                    f"scp failed for {local.name}: {(r.stderr or r.stdout)[:300]}",
                    exit_code=1,
                )

        remote_cmd = (
            f"chmod +x {remote_dir}/install.sh && "
            f"bash {remote_dir}/install.sh "
            f"{remote_dir}/{wheel.name} {remote_dir}/cluster.toml {remote_dir}/cluster.pub; "
            f"ec=$?; rm -rf {remote_dir}; exit $ec"
        )
        run = ctx.runner.run(
            ssh_bind_argv(
                abs_ssh,
                bind_ip=self_ip,
                peer_ip=peer_ip,
                user=u,
                connect_timeout=15,
                remote=(remote_cmd,),
            ),
            timeout=timeout,
        )
        log = ((run.stdout or "") + "\n" + (run.stderr or "")).strip()
        ok = run.returncode == 0
        return RemoteInstallResult(
            peer_id=peer_node.id,
            peer_ip=peer_ip,
            bind_ip=self_ip,
            ssh_target=target,
            wheel=str(wheel),
            ok=ok,
            message="ok" if ok else f"remote install failed rc={run.returncode}",
            log=log[-4000:],
        )
