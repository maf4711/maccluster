"""Re-plan after a partial rung: re-stat both sides so nothing is sent twice.

Used by ``sync_transport.run_transfer_ladder`` before it falls back to the
next rung. Only the rels of the current plan are stat'ed — locally with
``os.lstat``, on the peer with a tiny shipped script — and ``plan_transfers``
decides again what is still missing.
"""

from __future__ import annotations

import os
import stat
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

from maccluster.app_factory import AppContext
from maccluster.render.progress import NullProgress, ProgressLike

if TYPE_CHECKING:
    from maccluster.services.sync_service import FileMeta
    from maccluster.services.sync_transport import TransferPlan, TransferTarget

__all__ = ["replan_remaining"]

_RESTAT_TIMEOUT = 600.0

# Peer-side stat of the planned rels only → same ``rel\tmtime_ns\tsize`` lines
# as the inventory walk, so ``parse_inventory_text`` reads them back.
_REMOTE_STAT_PY = """import os, sys

home, list_path = sys.argv[1], sys.argv[2]
with open(list_path, encoding="utf-8") as fh:
    for line in fh:
        rel = line.rstrip("\\n")
        if not rel or ".." in rel.split("/"):
            continue
        try:
            st = os.lstat(os.path.join(home, rel))
        except OSError:
            continue
        sys.stdout.write("%s\\t%d\\t%d\\n" % (rel, st.st_mtime_ns, st.st_size))
"""


# --- re-plan after a partial run ---------------------------------------------------------


def _stat_local(home: Path, rels: Sequence[str]) -> dict[str, FileMeta]:
    from maccluster.services.sync_service import FileMeta

    out: dict[str, FileMeta] = {}
    for rel in rels:
        if ".." in rel.split("/"):
            continue
        try:
            st = os.lstat(home / rel)
        except OSError:
            continue
        if stat.S_ISREG(st.st_mode) or stat.S_ISLNK(st.st_mode):
            out[rel] = FileMeta(mtime_ns=st.st_mtime_ns, size=st.st_size)
    return out


def _stat_remote(
    ctx: AppContext,
    target: TransferTarget,
    rels: Sequence[str],
    *,
    ssh_target: str,
    bind_ip: str | None,
    timeout: float,
    work: Path,
) -> dict[str, FileMeta] | None:
    """Stat *rels* on the peer via a tiny shipped script; None when ssh/scp failed."""
    from maccluster.services.sync_service import _scp_argv, _ssh_argv, parse_inventory_text

    abs_ssh, abs_scp = ctx.runner.resolve("ssh"), ctx.runner.resolve("scp")
    work.mkdir(parents=True, exist_ok=True)
    script, listing = work / "restat.py", work / "restat_list.txt"
    script.write_text(_REMOTE_STAT_PY, encoding="utf-8")
    listing.write_text("".join(f"{r}\n" for r in rels), encoding="utf-8")
    remote_script = f"/tmp/maccluster-restat-{os.getpid()}.py"
    remote_list = f"/tmp/maccluster-restat-{os.getpid()}.txt"
    for local, remote in ((script, remote_script), (listing, remote_list)):
        scp = ctx.runner.run(
            _scp_argv(abs_scp, str(local), f"{ssh_target}:{remote}", bind_ip=bind_ip),
            timeout=min(timeout, 60.0),
        )
        if scp.returncode != 0:
            return None
    res = ctx.runner.run(
        _ssh_argv(
            abs_ssh,
            ssh_target,
            "/usr/bin/python3",
            remote_script,
            target.remote_home,
            remote_list,
            bind_ip=bind_ip,
        ),
        timeout=min(timeout, _RESTAT_TIMEOUT),
    )
    ctx.runner.run(
        _ssh_argv(
            abs_ssh, ssh_target, "/bin/rm", "-f", remote_script, remote_list, bind_ip=bind_ip
        ),
        timeout=30.0,
    )
    if res.returncode != 0:
        return None
    return parse_inventory_text(res.stdout or "")


def replan_remaining(
    ctx: AppContext,
    plan: TransferPlan,
    target: TransferTarget,
    *,
    rung: str,
    timeout: float,
    work: Path,
    progress: ProgressLike | None = None,
) -> TransferPlan:
    """Drop files a partial run already moved: re-stat both sides, re-plan the subset.

    The peer is reached over *rung*'s ssh target (the one about to be used).
    When the peer cannot be re-stat'ed the full plan is kept — a duplicate copy
    costs time, a skipped file costs data.
    """
    from maccluster.services.sync_service import plan_transfers

    prog = progress or NullProgress()
    rels = sorted(set(plan.to_push) | set(plan.to_pull))
    if not rels:
        return plan
    ssh_target, bind_ip = target.ssh_for(rung)
    local_now = _stat_local(target.local_home, rels)
    remote_now = _stat_remote(
        ctx, target, rels, ssh_target=ssh_target, bind_ip=bind_ip, timeout=timeout, work=work
    )
    if remote_now is None:
        prog.note("  re-stat on peer failed — keeping the full plan (files may be sent twice)")
        return plan
    push_now, pull_now, _stats = plan_transfers(
        dict(local_now), dict(remote_now), policy=plan.policy, remote_complete=True
    )
    keep_push = [r for r in plan.to_push if r in set(push_now)]
    keep_pull = [r for r in plan.to_pull if r in set(pull_now)]
    prog.note(
        f"  remaining after partial run: push {len(keep_push)}/{len(plan.to_push)} · "
        f"pull {len(keep_pull)}/{len(plan.to_pull)}"
    )
    return replace(
        plan,
        to_push=keep_push,
        to_pull=keep_pull,
        push_sizes={r: int(plan.push_sizes.get(r, local_now[r].size)) for r in keep_push},
        pull_sizes={r: int(plan.pull_sizes.get(r, remote_now[r].size)) for r in keep_pull},
        local_inv={**plan.local_inv, **local_now},
        remote_inv={**plan.remote_inv, **remote_now},
    )
