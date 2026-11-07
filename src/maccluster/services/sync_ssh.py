"""SSH/SCP argv builders and single-file scp for the home sync.

Extracted verbatim from ``sync_service``. One shared option set keeps the
non-interactive auth policy and the TB-bridge ``BindAddress`` identical across
preflight, inventory, staging and archive transfers.
"""

from __future__ import annotations

import shlex
from pathlib import Path

from maccluster.app_factory import AppContext
from maccluster.constants import TIMEOUT_SSH


def _ssh_argv(
    abs_ssh: str,
    ssh_target: str,
    *remote: str,
    connect_timeout: int = 8,
    bind_ip: str | None = None,
) -> list[str]:
    """SSH argv. When bind_ip is set (cluster Self-IP), force TB bridge source."""
    argv: list[str] = [
        abs_ssh,
        "-o",
        "BatchMode=yes",
        "-o",
        "PasswordAuthentication=no",
        "-o",
        "KbdInteractiveAuthentication=no",
        "-o",
        "StrictHostKeyChecking=accept-new",
        "-o",
        f"ConnectTimeout={connect_timeout}",
        "-o",
        "ControlMaster=no",
        "-o",
        "ControlPath=none",
    ]
    if bind_ip:
        argv.extend(["-o", f"BindAddress={bind_ip}", "-b", bind_ip])
    argv.append(ssh_target)
    argv.extend(remote)
    return argv


def _scp_argv(
    abs_scp: str,
    *parts: str,
    connect_timeout: int = 8,
    bind_ip: str | None = None,
) -> list[str]:
    argv: list[str] = [
        abs_scp,
        "-o",
        "BatchMode=yes",
        "-o",
        "PasswordAuthentication=no",
        "-o",
        "KbdInteractiveAuthentication=no",
        "-o",
        "StrictHostKeyChecking=accept-new",
        "-o",
        f"ConnectTimeout={connect_timeout}",
        "-o",
        "ControlMaster=no",
        "-o",
        "ControlPath=none",
    ]
    if bind_ip:
        argv.extend(["-o", f"BindAddress={bind_ip}"])
    argv.extend(parts)
    return argv


def _preflight_ssh(
    ctx: AppContext,
    abs_ssh: str,
    ssh_target: str,
    *,
    timeout: float = TIMEOUT_SSH,
    bind_ip: str | None = None,
) -> str | None:
    result = ctx.runner.run(
        _ssh_argv(
            abs_ssh,
            ssh_target,
            "/usr/bin/true",
            connect_timeout=max(1, int(timeout)),
            bind_ip=bind_ip,
        ),
        timeout=timeout + 2.0,
    )
    if result.returncode == 0:
        return None
    detail = (result.stderr or result.stdout or "ssh failed").strip()[:300]
    return detail or f"ssh exit {result.returncode}"


def _ssh_cat_write_argv(
    abs_ssh: str, ssh_target: str, remote_path: str, *, bind_ip: str | None = None
) -> list[str]:
    cmd = f"cat > {shlex.quote(remote_path)}"
    return _ssh_argv(abs_ssh, ssh_target, "/bin/sh", "-c", cmd, bind_ip=bind_ip)


def _ssh_cat_read_argv(
    abs_ssh: str, ssh_target: str, remote_path: str, *, bind_ip: str | None = None
) -> list[str]:
    cmd = f"cat {shlex.quote(remote_path)}"
    return _ssh_argv(abs_ssh, ssh_target, "/bin/sh", "-c", cmd, bind_ip=bind_ip)


def _scp_one_file(
    ctx: AppContext,
    *,
    abs_scp: str,
    ssh_target: str,
    remote_path: str,
    local_path: Path,
    direction: str,
    timeout: float,
    bind_ip: str | None,
) -> tuple[int, str]:
    """direction: pull = remote→local, push = local→remote."""
    local_path.parent.mkdir(parents=True, exist_ok=True)
    if direction == "pull":
        argv = _scp_argv(
            abs_scp,
            f"{ssh_target}:{remote_path}",
            str(local_path),
            bind_ip=bind_ip,
        )
    else:
        argv = _scp_argv(
            abs_scp,
            str(local_path),
            f"{ssh_target}:{remote_path}",
            bind_ip=bind_ip,
        )
    r = ctx.runner.run(argv, timeout=timeout)
    if r.returncode != 0:
        return r.returncode, (r.stderr or r.stdout or "scp failed")[:400]
    return 0, ""
