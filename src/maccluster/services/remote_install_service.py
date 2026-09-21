"""Install MacCluster on a peer over the TB bridge only (SSH + local wheel)."""

from __future__ import annotations

import hashlib
import os
import re
import shlex
import shutil
import subprocess
import tempfile
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
from maccluster.services.remote_install_script import REMOTE_INSTALL_SH


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
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=False,
                stdin=subprocess.DEVNULL,
                timeout=600,
                env={**os.environ, "PIP_NO_INPUT": "1", "GIT_TERMINAL_PROMPT": "0"},
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise CliError(f"cannot build wheel: {exc}", exit_code=1) from exc
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
    wheel_path: Path | None = None,
    preflight_speedtest: bool = True,
    preserve_config: bool = False,
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
    if peer_ip == self_ip:
        raise CliError("remote-install target is this Mac; use update instead", exit_code=2)
    u = resolve_install_user(user, peer_node)
    target = cluster_target(u, peer_ip)

    if dry_run:
        return RemoteInstallResult(
            peer_id=peer_node.id,
            peer_ip=peer_ip,
            bind_ip=self_ip,
            ssh_target=target,
            wheel=str(wheel_path) if wheel_path else "(build local wheel)",
            ok=True,
            message=f"dry-run: would install on {target} via BindAddress {self_ip}",
        )

    if setup_ssh_config:
        write_cluster_ssh_config(self_ip=self_ip, subnet=subnet, user=u)

    # Startup cable + speed check (TB path grade; iperf if peer SSH allows)
    if preflight_speedtest:
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
        wheel = Path(wheel_path).resolve() if wheel_path else find_or_build_wheel(work=work)
        if not wheel.is_file():
            raise CliError(f"wheel missing: {wheel}", exit_code=2)
        cfg_path = ctx.config_path
        if copy_config and not Path(cfg_path).is_file():
            raise CliError(f"config missing: {cfg_path}", exit_code=2)
        pubkey = read_pubkey()
        pub_file = work / "id_cluster.pub"
        pub_file.write_text(pubkey, encoding="utf-8")
        script = work / "remote_install_peer.sh"
        script.write_text(REMOTE_INSTALL_SH.lstrip(), encoding="utf-8")

        create = ctx.runner.run(
            ssh_bind_argv(
                abs_ssh,
                bind_ip=self_ip,
                peer_ip=peer_ip,
                user=u,
                remote=("/usr/bin/mktemp -d /tmp/maccluster-install.XXXXXXXX",),
            ),
            timeout=30.0,
        )
        remote_dir = create.stdout.strip()
        if create.returncode != 0 or not re.fullmatch(
            r"/tmp/maccluster-install\.[A-Za-z0-9]+", remote_dir
        ):
            raise CliError("cannot create private peer install directory", exit_code=1)
        try:
            files = {
                wheel: f"{remote_dir}/{wheel.name}",
                pub_file: f"{remote_dir}/cluster.pub",
                script: f"{remote_dir}/install.sh",
            }
            remote_cfg = "-"
            if copy_config:
                remote_cfg = f"{remote_dir}/cluster.toml"
                files[Path(cfg_path)] = remote_cfg
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
                        f"scp failed for {local.name}: {(r.stderr or r.stdout)[:300]}", exit_code=1
                    )
            with wheel.open("rb") as fh:
                digest = hashlib.file_digest(fh, "sha256").hexdigest()
            remote_cmd = shlex.join(
                [
                    "/bin/bash",
                    f"{remote_dir}/install.sh",
                    f"{remote_dir}/{wheel.name}",
                    remote_cfg,
                    f"{remote_dir}/cluster.pub",
                    digest,
                    "1" if preserve_config else "0",
                ]
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
            return RemoteInstallResult(
                peer_id=peer_node.id,
                peer_ip=peer_ip,
                bind_ip=self_ip,
                ssh_target=target,
                wheel=str(wheel),
                ok=run.returncode == 0,
                message="ok"
                if run.returncode == 0
                else f"remote install failed rc={run.returncode}",
                log=log[-4000:],
            )
        finally:
            # Cleanup failure must not hide the original install result.
            try:
                ctx.runner.run(
                    ssh_bind_argv(
                        abs_ssh,
                        bind_ip=self_ip,
                        peer_ip=peer_ip,
                        user=u,
                        remote=(f"/bin/rm -rf -- {shlex.quote(remote_dir)}",),
                    ),
                    timeout=30.0,
                )
            except Exception:
                pass
