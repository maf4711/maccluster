"""Host preparation around a sync run: disk-free probes, APFS snapshot,
iCloud materialisation and the failure notification.

Extracted verbatim from ``sync_service``; ``sync_home`` calls these before
and after the inventory/transfer phases.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

from maccluster.app_factory import AppContext
from maccluster.domain.models import Node
from maccluster.errors import CliError
from maccluster.render.progress import ProgressLike
from maccluster.services.sync_ssh import _scp_argv, _ssh_argv


def _free_bytes(path: Path) -> int | None:
    try:
        return int(shutil.disk_usage(path).free)
    except OSError:
        return None


def _remote_free_bytes(
    ctx: AppContext,
    abs_ssh: str,
    ssh_target: str,
    remote_home: str,
    *,
    bind_ip: str | None,
) -> int | None:
    # Pure python on peer for free space of volume containing remote_home
    py = (
        "import os,sys;"
        f"p={remote_home!r};"
        "st=os.statvfs(p if os.path.isdir(p) else os.path.dirname(p) or '/');"
        "print(st.f_bavail*st.f_frsize)"
    )
    r = ctx.runner.run(
        _ssh_argv(abs_ssh, ssh_target, "/usr/bin/python3", "-c", py, bind_ip=bind_ip),
        timeout=30.0,
    )
    if r.returncode != 0:
        return None
    try:
        return int((r.stdout or "").strip().split()[0])
    except (ValueError, IndexError):
        return None


def _maybe_apfs_snapshot(ctx: AppContext, *, enabled: bool) -> str | None:
    if not enabled:
        return None
    try:
        abs_tm = ctx.runner.resolve("tmutil")
    except CliError:
        return None
    r = ctx.runner.run([abs_tm, "localsnapshot"], timeout=120.0)
    if r.returncode != 0:
        return None
    out = (r.stdout or r.stderr or "").strip()
    return out[:200] or "localsnapshot ok"


def _notify_fail(ctx: AppContext, title: str, body: str) -> None:
    try:
        abs_osa = ctx.runner.resolve("osascript")
    except CliError:
        return
    safe_title = title.replace("\\", "\\\\").replace('"', '\\"')
    safe_body = body.replace("\\", "\\\\").replace('"', '\\"')[:180]
    ctx.runner.run(
        [
            abs_osa,
            "-e",
            f'display notification "{safe_body}" with title "{safe_title}"',
        ],
        timeout=15.0,
    )


def _run_force_icloud(
    ctx: AppContext,
    *,
    local_home: Path,
    peers: list[tuple[Node, str]],
    abs_ssh: str,
    abs_scp: str,
    bind_ip: str,
    timeout_per_file: float,
    max_seconds: float,
    prog: ProgressLike,
) -> None:
    """Materialize iCloud dataless stubs on local + peers before inventory."""
    from maccluster.services.icloud_materialize import (
        REMOTE_MATERIALIZE_PY,
        default_icloud_roots,
        materialize_tree,
    )

    prog.phase("icloud", direction="", detail="materialize local")
    for root in default_icloud_roots(local_home):
        mr = materialize_tree(
            root,
            timeout_per_file=timeout_per_file,
            max_seconds=max_seconds,
            note=prog.note,
        )
        prog.note(
            f"  local {root.name}: mat={mr.materialized} fail={mr.failed} "
            f"remaining_dataless={mr.remaining_dataless}"
        )

    for node, ssh_target in peers:
        prog.phase("icloud", direction="", detail=f"materialize {node.id}")
        prog.note(f"icloud: materialize on peer {node.id} ({ssh_target})")
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as tf:
            tf.write(REMOTE_MATERIALIZE_PY)
            local_script = tf.name
        remote_script = f"/tmp/maccluster_icloud_mat_{os.getpid()}_{node.id}.py"
        try:
            scp_r = ctx.runner.run(
                _scp_argv(
                    abs_scp,
                    local_script,
                    f"{ssh_target}:{remote_script}",
                    connect_timeout=15,
                    bind_ip=bind_ip,
                ),
                timeout=60.0,
            )
            if scp_r.returncode != 0:
                prog.note(
                    f"  peer {node.id}: scp materialize script failed: "
                    f"{(scp_r.stderr or scp_r.stdout or '')[:160]}"
                )
                continue
            mat_r = ctx.runner.run(
                _ssh_argv(
                    abs_ssh,
                    ssh_target,
                    "python3",
                    remote_script,
                    str(timeout_per_file),
                    str(max_seconds),
                    "Desktop",
                    "Documents",
                    connect_timeout=15,
                    bind_ip=bind_ip,
                ),
                timeout=max_seconds * 2 + 120.0,
            )
            out = (mat_r.stdout or mat_r.stderr or "").strip()
            for line in out.splitlines()[-10:]:
                prog.note(f"  peer {node.id}: {line}")
            if mat_r.returncode != 0:
                prog.note(f"  peer {node.id}: materialize rc={mat_r.returncode}")
        finally:
            try:
                os.unlink(local_script)
            except OSError:
                pass
