"""Home directory two-way sync over TB/SSH using Apple ditto (newest-wins).

Apple's ``ditto`` is the system tool that preserves resource forks, extended
attributes, ACLs, and quarantine bits by default — preferred over third-party
rsync for macOS Home fidelity. Newest-wins is decided by comparing mtimes;
only newer/missing files are staged and transferred as a ditto CPIO archive
over SSH/SCP. Nothing is deleted.

Cloud alternative (not used here): iCloud Drive / Desktop & Documents — needs
Apple ID and internet; this path stays on the Thunderbolt mesh.
"""

from __future__ import annotations

import getpass
import os
import shlex
import shutil
import tempfile
import time
from dataclasses import replace
from pathlib import Path

from maccluster.app_factory import AppContext
from maccluster.constants import (
    DEVELOPER_DIR_NAME,
    SYNC_DEV_EXCLUDES,
    SYNC_HOME_EXCLUDES,
    TIMEOUT_SYNC,
)
from maccluster.domain.models import Node, SyncHomeResult, SyncPeerResult
from maccluster.errors import CliError
from maccluster.render.progress import NullProgress, ProgressLike, format_bytes, format_rate
from maccluster.services import sync_transport
from maccluster.services.config_service import load_and_bind_self

# Re-exports: the split modules own these names; callers and tests still
# import them from sync_service, so they stay importable here (noqa: F401 on
# the ones this module itself no longer calls).
from maccluster.services.sync_inventory import (
    _INV_PREF,  # noqa: F401
    _INV_SKIP_NAMES,  # noqa: F401
    _REMOTE_INVENTORY_PY,  # noqa: F401
    _UF_DATALESS,  # noqa: F401
    FileMeta,
    _inv_skip_names,  # noqa: F401
    _norm_rel,  # noqa: F401
    _remote_inventory,
    _safe_scandir,  # noqa: F401
    inventory_local,
    is_excluded,  # noqa: F401
    parse_inventory_text,  # noqa: F401
)
from maccluster.services.sync_plan import (
    SYNC_CHUNK_BYTES,  # noqa: F401
    SYNC_CHUNK_FILES,  # noqa: F401
    SYNC_LARGE_FILE_BYTES,  # noqa: F401
    DeltaBucket,  # noqa: F401
    PreciseDelta,  # noqa: F401
    _bucket_from,  # noqa: F401
    _bytes_for_rels,  # noqa: F401
    _chunk_rels,
    _sample_list,
    _split_large_files,
    apply_batch_limits,
    classify_compare,  # noqa: F401
    format_precise_delta,
    plan_transfers,
    precise_delta,
)
from maccluster.services.sync_ssh import (
    _preflight_ssh,
    _scp_argv,
    _scp_one_file,
    _ssh_argv,
    _ssh_cat_read_argv,  # noqa: F401
    _ssh_cat_write_argv,  # noqa: F401
)
from maccluster.services.sync_wifi import wifi_ssh_target

_REMOTE_STAGE_PY = 'import os, stat, subprocess, sys\n\nhome, list_path, stage, archive = sys.argv[1:5]\nos.makedirs(stage, exist_ok=True)\nUF_DATALESS = 0x40000000\nn = 0\nskipped = 0\nwith open(list_path, encoding="utf-8") as fh:\n    for line in fh:\n        rel = line.strip()\n        if not rel or ".." in rel.split("/"):\n            continue\n        src = os.path.join(home, rel)\n        dst = os.path.join(stage, rel)\n        if not os.path.lexists(src):\n            skipped += 1\n            continue\n        try:\n            st = os.lstat(src)\n        except OSError:\n            skipped += 1\n            continue\n        if getattr(st, "st_flags", 0) & UF_DATALESS:\n            skipped += 1\n            continue\n        if not (stat.S_ISREG(st.st_mode) or stat.S_ISLNK(st.st_mode)):\n            skipped += 1\n            continue\n        # Unreadable dataless-ish edge cases\n        if not os.access(src, os.R_OK) and not stat.S_ISLNK(st.st_mode):\n            skipped += 1\n            continue\n        os.makedirs(os.path.dirname(dst) or ".", exist_ok=True)\n        if os.path.lexists(dst):\n            try:\n                os.unlink(dst)\n            except OSError:\n                pass\n        ok = False\n        try:\n            os.link(src, dst)\n            ok = True\n        except OSError:\n            try:\n                r = subprocess.run(\n                    ["/bin/cp", "-p", src, dst],\n                    stdout=subprocess.DEVNULL,\n                    stderr=subprocess.DEVNULL,\n                    timeout=30,\n                    check=False,\n                )\n                ok = r.returncode == 0 and os.path.lexists(dst)\n            except Exception:\n                ok = False\n        if ok:\n            n += 1\n        else:\n            skipped += 1\n\nif n == 0:\n    print("staged=0 skipped=%d archive_rc=0" % skipped, flush=True)\n    open(archive, "wb").close()\n    sys.exit(0)\n\nrc = 1\ntry:\n    rc = subprocess.run(\n        ["/usr/bin/ditto", "-c", stage, archive],\n        timeout=max(120, min(3600, n // 10 + 60)),\n        check=False,\n    ).returncode\nexcept Exception:\n    rc = 1\n\narch_ok = os.path.isfile(archive) and os.path.getsize(archive) > 0\nif rc != 0 and arch_ok:\n    rc = 0\nprint("staged=%d skipped=%d archive_rc=%d" % (n, skipped, rc), flush=True)\n# Soft-ok empty transfer only when nothing staged; never claim success with missing archive\nif n > 0 and not arch_ok:\n    sys.exit(1)\nsys.exit(0 if arch_ok or n == 0 else rc)\n'


def normalize_sync_target(action: str | None) -> str | None:
    """Map CLI aliases onto the canonical sync target (`home` | `dev`)."""
    if action is None:
        return None
    key = str(action).strip().lower()
    if key in ("dev", "developer"):
        return "dev"
    if key == "home":
        return "home"
    return key or None


def resolve_sync_tree(action: str, home: str | Path | None) -> Path:
    """Local tree root: ``~/Developer`` for `dev`, ``~`` for `home`, or ``--home``."""
    if home is not None and str(home).strip():
        return Path(home).expanduser()
    target = normalize_sync_target(action) or "home"
    if target == "dev":
        return Path.home() / DEVELOPER_DIR_NAME
    return Path.home()


def log_home_for_target(target: str, tree: Path) -> Path:
    """Where run logs live. Developer-tree sync stays in the real user home."""
    if normalize_sync_target(target) == "dev":
        return Path.home()
    return tree


def _ssh_target_for(node: Node, *, default_user: str) -> str:
    if node.ssh_target:
        return node.ssh_target.strip()
    return f"{default_user}@{node.ip}"


def _resolve_peers(
    cfg_nodes: tuple[Node, ...],
    self_node: Node,
    *,
    peer_filter: str | None,
    default_user: str,
    peer_limit: int | None = None,
) -> list[tuple[Node, str]]:
    peers: list[tuple[Node, str]] = []
    for n in cfg_nodes:
        if n.id == self_node.id:
            continue
        if peer_filter:
            if peer_filter not in (n.id, str(n.ip)):
                if not (n.ssh_target and peer_filter == n.ssh_target):
                    continue
        peers.append((n, _ssh_target_for(n, default_user=default_user)))
    if peer_filter and not peers:
        raise CliError(
            f"no peer matched {peer_filter!r} (use node id or IP from cluster.toml)",
            exit_code=2,
        )
    if not peers:
        raise CliError("no peers in config to sync with", exit_code=2)
    if peer_limit is not None:
        if peer_limit < 1:
            raise CliError("--limit must be >= 1", exit_code=2)
        peers = peers[:peer_limit]
        if not peers:
            raise CliError("no peers left after --limit", exit_code=2)
    return peers


def _stage_hardlinks(
    home: Path,
    rels: list[str],
    stage: Path,
    *,
    abs_ditto: str,
    runner,
    timeout: float,
    progress: ProgressLike | None = None,
    direction: str = "push",
    sizes: dict[str, int] | None = None,
    bytes_base: int = 0,
    bytes_total: int = 0,
) -> tuple[int, int]:
    """Return (files_staged, bytes_staged)."""
    n = 0
    bytes_staged = 0
    total_files = len(rels)
    sizes = sizes or {}
    for i, rel in enumerate(rels, start=1):
        if ".." in rel.split("/"):
            continue
        src = home / rel
        dst = stage / rel
        if not src.exists() and not src.is_symlink():
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists() or dst.is_symlink():
            try:
                dst.unlink()
            except OSError:
                pass
        sz = sizes.get(rel, 0)
        if sz <= 0:
            try:
                sz = src.lstat().st_size
            except OSError:
                sz = 0
        ok = False
        try:
            os.link(src, dst)
            ok = True
        except OSError:
            r = runner.run([abs_ditto, str(src), str(dst)], timeout=min(timeout, 120.0))
            ok = r.returncode == 0
        if ok:
            n += 1
            bytes_staged += sz
            if progress is not None:
                progress.update(
                    phase="stage",
                    direction=direction,
                    path=rel,
                    file_index=i,
                    file_total=total_files,
                    files_done=n,
                    files_total=total_files,
                    bytes_done=bytes_base + bytes_staged,
                    bytes_total=bytes_total if bytes_total > 0 else bytes_base + bytes_staged,
                )
    return n, bytes_staged


def _transfer_large_files_pull(
    ctx: AppContext,
    *,
    abs_scp: str,
    ssh_target: str,
    local_home: Path,
    remote_home: str,
    rels: list[str],
    sizes: dict[str, int],
    timeout: float,
    progress: ProgressLike | None,
    bytes_base: int,
    bytes_total: int,
    bind_ip: str | None,
) -> tuple[int, str, str, int]:
    prog = progress or NullProgress()
    done = 0
    for i, rel in enumerate(rels, 1):
        sz = int(sizes.get(rel, 0) or 0)
        prog.note(f"pull large file {i}/{len(rels)}: {rel} ({format_bytes(sz)})")
        prog.phase("transfer", direction="pull", detail=f"scp large {format_bytes(sz)}")
        dest = local_home / rel
        remote = f"{remote_home.rstrip('/')}/{rel}"
        rc, err = _scp_one_file(
            ctx,
            abs_scp=abs_scp,
            ssh_target=ssh_target,
            remote_path=remote,
            local_path=dest,
            direction="pull",
            timeout=timeout,
            bind_ip=bind_ip,
        )
        if rc != 0:
            return rc, "", f"large pull failed {rel}: {err}", done
        done += sz
        prog.update(
            bytes_done=bytes_base + done,
            bytes_total=bytes_total or (bytes_base + done),
            path=rel,
            force=True,
        )
    return 0, f"pull large: {len(rels)} files ({format_bytes(done)}) via scp", "", done


def _transfer_large_files_push(
    ctx: AppContext,
    *,
    abs_scp: str,
    abs_ssh: str,
    ssh_target: str,
    local_home: Path,
    remote_home: str,
    rels: list[str],
    sizes: dict[str, int],
    timeout: float,
    progress: ProgressLike | None,
    bytes_base: int,
    bytes_total: int,
    bind_ip: str | None,
) -> tuple[int, str, str, int]:
    prog = progress or NullProgress()
    done = 0
    for i, rel in enumerate(rels, 1):
        sz = int(sizes.get(rel, 0) or 0)
        prog.note(f"push large file {i}/{len(rels)}: {rel} ({format_bytes(sz)})")
        prog.phase("transfer", direction="push", detail=f"scp large {format_bytes(sz)}")
        src = local_home / rel
        remote = f"{remote_home.rstrip('/')}/{rel}"
        # ensure remote parent exists
        parent = str(Path(remote).parent)
        ctx.runner.run(
            _ssh_argv(
                abs_ssh,
                ssh_target,
                f"mkdir -p {shlex.quote(parent)}",
                bind_ip=bind_ip,
            ),
            timeout=60.0,
        )
        rc, err = _scp_one_file(
            ctx,
            abs_scp=abs_scp,
            ssh_target=ssh_target,
            remote_path=remote,
            local_path=src,
            direction="push",
            timeout=timeout,
            bind_ip=bind_ip,
        )
        if rc != 0:
            return rc, "", f"large push failed {rel}: {err}", done
        done += sz
        prog.update(
            bytes_done=bytes_base + done,
            bytes_total=bytes_total or (bytes_base + done),
            path=rel,
            force=True,
        )
    return 0, f"push large: {len(rels)} files ({format_bytes(done)}) via scp", "", done


def _transfer_push_once(
    ctx: AppContext,
    *,
    abs_ditto: str,
    abs_ssh: str,
    abs_scp: str,
    ssh_target: str,
    local_home: Path,
    remote_home: str,
    rels: list[str],
    sizes: dict[str, int],
    dry_run: bool,
    timeout: float,
    work: Path,
    progress: ProgressLike | None = None,
    bytes_base: int = 0,
    bytes_total: int = 0,
    bind_ip: str | None = None,
    stream: bool = True,
) -> tuple[int, str, str, int]:
    """Returns (rc, stdout, stderr, bytes_transferred_estimate).

    With *stream* the archive is piped straight into the peer's ``ditto -x``.
    The file-based path writes push.cpio locally, copies it, then unpacks it —
    three serial passes over the same bytes, with the link idle during two of
    them. Measured on this cluster: the link carried data in 27% of wall time.
    """
    prog = progress or NullProgress()
    payload = sum(sizes.get(r, 0) for r in rels)
    if not rels:
        return 0, "push: 0 files", "", 0
    if dry_run:
        prog.note(f"push dry-run: {len(rels)} files ({format_bytes(payload)})")
        return 0, _sample_list(rels, label="push dry-run"), "", payload

    stage = work / "push_stage"
    archive = work / "push.cpio"
    if stage.exists():
        shutil.rmtree(stage, ignore_errors=True)
    stage.mkdir(parents=True)

    prog.phase("stage", direction="push", detail=f"{len(rels)} files")
    n, staged_bytes = _stage_hardlinks(
        local_home,
        rels,
        stage,
        abs_ditto=abs_ditto,
        runner=ctx.runner,
        timeout=timeout,
        progress=prog,
        direction="push",
        sizes=sizes,
        bytes_base=bytes_base,
        bytes_total=bytes_total,
    )
    if n == 0:
        return 0, "push: nothing staged", "", 0

    remote_dest = shlex.quote(remote_home)
    if stream:
        prog.phase(
            "stream",
            direction="push",
            detail=f"ditto -c | ssh ditto -x ({format_bytes(staged_bytes)})",
        )
        prog.update(
            path=f"→ {ssh_target}:{remote_home}",
            bytes_done=bytes_base,
            bytes_total=bytes_base + staged_bytes if bytes_total <= 0 else bytes_total,
            force=True,
        )
        pipe = ctx.runner.run_pipe(
            [abs_ditto, "-c", str(stage), "-"],
            _ssh_argv(
                abs_ssh,
                ssh_target,
                f"mkdir -p {remote_dest} && /usr/bin/ditto -x - {remote_dest}",
                bind_ip=bind_ip,
            ),
            timeout=timeout,
        )
        if pipe.returncode != 0:
            return (
                pipe.returncode,
                f"push staged={n}",
                (pipe.stderr or pipe.stdout or "push stream failed")[:500],
                0,
            )
        prog.update(
            bytes_done=bytes_base + staged_bytes,
            path="push done",
            force=True,
        )
        return (
            0,
            f"push: {n} files ({format_bytes(staged_bytes)}) streamed via Apple ditto",
            "",
            staged_bytes,
        )

    prog.phase("archive", direction="push", detail="ditto -c")
    prog.update(
        path="(building CPIO)",
        bytes_done=bytes_base + staged_bytes // 2,
        bytes_total=bytes_total or staged_bytes,
        force=True,
    )
    cr = ctx.runner.run([abs_ditto, "-c", str(stage), str(archive)], timeout=timeout)
    if cr.returncode != 0:
        return cr.returncode, "", (cr.stderr or cr.stdout or "ditto -c failed")[:500], 0

    arch_size = archive.stat().st_size if archive.is_file() else staged_bytes
    remote_arch = f"/tmp/maccluster-push-{os.getpid()}.cpio"
    prog.phase("transfer", direction="push", detail=f"ssh cat {format_bytes(arch_size)}")
    prog.update(
        path=archive.name,
        bytes_done=bytes_base,
        bytes_total=bytes_base + arch_size if bytes_total <= 0 else bytes_total,
        force=True,
    )

    def on_push_chunk(done: int, total: int) -> None:
        prog.update(
            phase="transfer",
            direction="push",
            path=f"→ {ssh_target}:{remote_arch}",
            bytes_done=bytes_base + done,
            bytes_total=bytes_base + (total or arch_size) if bytes_total <= 0 else bytes_total,
        )

    # Prefer scp (reliable over TB); stream_stdin is optional progress path
    scp = ctx.runner.run(
        _scp_argv(
            abs_scp,
            str(archive),
            f"{ssh_target}:{remote_arch}",
            bind_ip=bind_ip,
        ),
        timeout=timeout,
    )
    on_push_chunk(arch_size, arch_size)
    if scp.returncode != 0:
        return scp.returncode, "", (scp.stderr or scp.stdout or "push transfer failed")[:500], 0

    prog.phase("extract", direction="push", detail="remote ditto -x")
    prog.update(path=f"ditto -x → {remote_home}", force=True)
    # mkdir -p dest; ditto -x archive dest (absolute paths, no shell metachar surprises)
    remote_cmd = (
        f"mkdir -p {shlex.quote(remote_home)} && "
        f"/usr/bin/ditto -x {shlex.quote(remote_arch)} {shlex.quote(remote_home)} && "
        f"/bin/rm -f {shlex.quote(remote_arch)}"
    )
    # One remote argv only — OpenSSH joins multiple args with spaces and breaks bash -lc
    ex = ctx.runner.run(
        _ssh_argv(abs_ssh, ssh_target, remote_cmd, bind_ip=bind_ip),
        timeout=timeout,
    )
    if ex.returncode != 0:
        return (
            ex.returncode,
            f"push staged={n}",
            (ex.stderr or ex.stdout or "remote ditto -x failed")[:500],
            arch_size,
        )
    prog.update(
        bytes_done=bytes_base + arch_size if bytes_total <= 0 else bytes_base + staged_bytes,
        path="push done",
        force=True,
    )
    return (
        0,
        f"push: {n} files ({format_bytes(staged_bytes)}) via Apple ditto",
        "",
        staged_bytes,
    )


def _transfer_pull_once(
    ctx: AppContext,
    *,
    abs_ditto: str,
    abs_ssh: str,
    abs_scp: str,
    ssh_target: str,
    local_home: Path,
    remote_home: str,
    rels: list[str],
    sizes: dict[str, int],
    dry_run: bool,
    timeout: float,
    work: Path,
    progress: ProgressLike | None = None,
    bytes_base: int = 0,
    bytes_total: int = 0,
    bind_ip: str | None = None,
) -> tuple[int, str, str, int]:
    prog = progress or NullProgress()
    payload = sum(sizes.get(r, 0) for r in rels)
    if not rels:
        return 0, "pull: 0 files", "", 0
    if dry_run:
        prog.note(f"pull dry-run: {len(rels)} files ({format_bytes(payload)})")
        return 0, _sample_list(rels, label="pull dry-run"), "", payload

    list_path = work / "pull_list.txt"
    list_path.write_text("\n".join(rels) + "\n", encoding="utf-8")
    remote_list = f"/tmp/maccluster-pull-list-{os.getpid()}.txt"
    remote_stage = f"/tmp/maccluster-pull-stage-{os.getpid()}"
    remote_arch = f"/tmp/maccluster-pull-{os.getpid()}.cpio"
    local_arch = work / "pull.cpio"
    remote_py_path = f"/tmp/maccluster-stage-{os.getpid()}.py"

    prog.phase("prepare", direction="pull", detail="upload file list")
    scp1 = ctx.runner.run(
        _scp_argv(abs_scp, str(list_path), f"{ssh_target}:{remote_list}", bind_ip=bind_ip),
        timeout=min(timeout, 120.0),
    )
    if scp1.returncode != 0:
        return scp1.returncode, "", (scp1.stderr or "scp list failed")[:500], 0

    remote_py = work / "remote_stage.py"
    remote_py.write_text(_REMOTE_STAGE_PY, encoding="utf-8")
    scp_py = ctx.runner.run(
        _scp_argv(abs_scp, str(remote_py), f"{ssh_target}:{remote_py_path}", bind_ip=bind_ip),
        timeout=min(timeout, 60.0),
    )
    if scp_py.returncode != 0:
        return scp_py.returncode, "", (scp_py.stderr or "scp stage script failed")[:500], 0

    prog.phase("stage", direction="pull", detail="remote hardlink + ditto -c")
    prog.update(
        path=f"{len(rels)} files on peer",
        files_done=0,
        files_total=len(rels),
        bytes_done=bytes_base,
        bytes_total=bytes_total or (bytes_base + payload),
        force=True,
    )
    stage_r = ctx.runner.run(
        _ssh_argv(
            abs_ssh,
            ssh_target,
            "env",
            "PYTHONUNBUFFERED=1",
            "/usr/bin/python3",
            "-u",
            remote_py_path,
            remote_home,
            remote_list,
            remote_stage,
            remote_arch,
            bind_ip=bind_ip,
        ),
        timeout=timeout,
    )
    out = (stage_r.stdout or "").strip()
    # staged=0 (all dataless/unreadable skipped) is success — nothing to pull
    if "staged=0" in out and stage_r.returncode == 0:
        return 0, f"pull: 0 files staged on peer ({out})", "", 0
    if stage_r.returncode != 0:
        return stage_r.returncode, out, (stage_r.stderr or out or "remote stage failed")[:500], 0

    # Optional remote archive size
    size_r = ctx.runner.run(
        _ssh_argv(
            abs_ssh,
            ssh_target,
            f"stat -f%z {shlex.quote(remote_arch)} 2>/dev/null || wc -c < {shlex.quote(remote_arch)}",
            bind_ip=bind_ip,
        ),
        timeout=30.0,
    )
    try:
        arch_size = int((size_r.stdout or "0").strip().split()[0])
    except (ValueError, IndexError):
        arch_size = payload

    prog.phase("transfer", direction="pull", detail=f"ssh cat {format_bytes(arch_size)}")

    def on_pull_chunk(done: int, total: int) -> None:
        prog.update(
            phase="transfer",
            direction="pull",
            path=f"← {ssh_target}:{remote_arch}",
            bytes_done=bytes_base + done,
            bytes_total=bytes_base + (total or arch_size) if bytes_total <= 0 else bytes_total,
        )

    scp2 = ctx.runner.run(
        _scp_argv(
            abs_scp,
            f"{ssh_target}:{remote_arch}",
            str(local_arch),
            bind_ip=bind_ip,
        ),
        timeout=timeout,
    )
    if scp2.returncode != 0:
        return scp2.returncode, out, (scp2.stderr or "pull transfer failed")[:500], 0
    got = local_arch.stat().st_size if local_arch.is_file() else 0
    on_pull_chunk(got, got if got else arch_size)
    if arch_size > 0 and got > 0 and abs(got - arch_size) > 1024:
        return (
            1,
            out,
            f"pull archive size mismatch local={got} remote={arch_size}",
            got,
        )
    if got == 0 and arch_size > 0:
        return 1, out, f"pull archive empty (remote claimed {arch_size} B)", 0

    prog.phase("extract", direction="pull", detail="local ditto -x")
    prog.update(path=f"ditto -x → {local_home}", force=True)
    local_home.mkdir(parents=True, exist_ok=True)
    ex = ctx.runner.run(
        [abs_ditto, "-x", str(local_arch), str(local_home)],
        timeout=timeout,
    )
    ctx.runner.run(
        _ssh_argv(
            abs_ssh,
            ssh_target,
            (
                f"/bin/rm -rf {shlex.quote(remote_stage)} {shlex.quote(remote_arch)} "
                f"{shlex.quote(remote_list)} {shlex.quote(remote_py_path)}"
            ),
            bind_ip=bind_ip,
        ),
        timeout=60.0,
    )
    if ex.returncode != 0:
        return ex.returncode, out, (ex.stderr or "local ditto -x failed")[:500], got
    prog.update(
        bytes_done=bytes_base + got if bytes_total <= 0 else min(bytes_total, bytes_base + payload),
        path="pull done",
        force=True,
    )
    return (
        0,
        f"pull: {len(rels)} files ({format_bytes(payload)}) via Apple ditto ({out})",
        "",
        payload,
    )


def _transfer_pull(
    ctx: AppContext,
    *,
    abs_ditto: str,
    abs_ssh: str,
    abs_scp: str,
    ssh_target: str,
    local_home: Path,
    remote_home: str,
    rels: list[str],
    sizes: dict[str, int],
    dry_run: bool,
    timeout: float,
    work: Path,
    progress: ProgressLike | None = None,
    bytes_base: int = 0,
    bytes_total: int = 0,
    bind_ip: str | None = None,
) -> tuple[int, str, str, int]:
    """Pull with direct scp for huge files + CPIO auto-batching for the rest."""
    prog = progress or NullProgress()
    if not rels:
        return 0, "pull: 0 files", "", 0
    payload = sum(int(sizes.get(r, 0) or 0) for r in rels)
    normal, large = _split_large_files(rels, sizes)
    done = 0
    outs: list[str] = []
    if dry_run:
        msg = _sample_list(rels, label="pull dry-run")
        if large:
            msg += f" (large-direct={len(large)})"
        return 0, msg, "", payload

    if large:
        rc, out, err, got = _transfer_large_files_pull(
            ctx,
            abs_scp=abs_scp,
            ssh_target=ssh_target,
            local_home=local_home,
            remote_home=remote_home,
            rels=large,
            sizes=sizes,
            timeout=timeout,
            progress=prog,
            bytes_base=bytes_base,
            bytes_total=bytes_total or (bytes_base + payload),
            bind_ip=bind_ip,
        )
        if rc != 0:
            return rc, out, err, done
        done += got
        if out:
            outs.append(out)

    if not normal:
        return 0, "; ".join(outs) or "pull: 0 files", "", done

    n_sizes = {r: sizes[r] for r in normal if r in sizes}
    batches = _chunk_rels(normal, n_sizes)
    for i, batch in enumerate(batches, 1):
        bsz = {r: n_sizes[r] for r in batch if r in n_sizes}
        b_payload = sum(bsz.values())
        if len(batches) > 1:
            prog.note(
                f"pull batch {i}/{len(batches)}: {len(batch)} files ({format_bytes(b_payload)})"
            )
        bwork = work / f"pull_batch_{i}"
        bwork.mkdir(parents=True, exist_ok=True)
        rc, out, err, got = _transfer_pull_once(
            ctx,
            abs_ditto=abs_ditto,
            abs_ssh=abs_ssh,
            abs_scp=abs_scp,
            ssh_target=ssh_target,
            local_home=local_home,
            remote_home=remote_home,
            rels=batch,
            sizes=bsz,
            dry_run=False,
            timeout=timeout,
            work=bwork,
            progress=prog,
            bytes_base=bytes_base + done,
            bytes_total=bytes_total or (bytes_base + payload),
            bind_ip=bind_ip,
        )
        if rc != 0:
            return rc, out, err or f"pull batch {i}/{len(batches)} failed", done
        done += got
        if out:
            outs.append(out)
    return (
        0,
        f"pull: {len(rels)} files ({format_bytes(payload)}); " + (outs[0] if outs else "ok"),
        "",
        done,
    )


def _transfer_push(
    ctx: AppContext,
    *,
    abs_ditto: str,
    abs_ssh: str,
    abs_scp: str,
    ssh_target: str,
    local_home: Path,
    remote_home: str,
    rels: list[str],
    sizes: dict[str, int],
    dry_run: bool,
    timeout: float,
    work: Path,
    progress: ProgressLike | None = None,
    bytes_base: int = 0,
    bytes_total: int = 0,
    bind_ip: str | None = None,
    stream: bool = True,
) -> tuple[int, str, str, int]:
    """Push with direct scp for huge files + CPIO auto-batching for the rest."""
    prog = progress or NullProgress()
    if not rels:
        return 0, "push: 0 files", "", 0
    payload = sum(int(sizes.get(r, 0) or 0) for r in rels)
    normal, large = _split_large_files(rels, sizes)
    done = 0
    outs: list[str] = []
    if dry_run:
        msg = _sample_list(rels, label="push dry-run")
        if large:
            msg += f" (large-direct={len(large)})"
        return 0, msg, "", payload

    if large:
        rc, out, err, got = _transfer_large_files_push(
            ctx,
            abs_scp=abs_scp,
            abs_ssh=abs_ssh,
            ssh_target=ssh_target,
            local_home=local_home,
            remote_home=remote_home,
            rels=large,
            sizes=sizes,
            timeout=timeout,
            progress=prog,
            bytes_base=bytes_base,
            bytes_total=bytes_total or (bytes_base + payload),
            bind_ip=bind_ip,
        )
        if rc != 0:
            return rc, out, err, done
        done += got
        if out:
            outs.append(out)

    if not normal:
        return 0, "; ".join(outs) or "push: 0 files", "", done

    n_sizes = {r: sizes[r] for r in normal if r in sizes}
    batches = _chunk_rels(normal, n_sizes)
    for i, batch in enumerate(batches, 1):
        bsz = {r: n_sizes[r] for r in batch if r in n_sizes}
        b_payload = sum(bsz.values())
        if len(batches) > 1:
            prog.note(
                f"push batch {i}/{len(batches)}: {len(batch)} files ({format_bytes(b_payload)})"
            )
        bwork = work / f"push_batch_{i}"
        bwork.mkdir(parents=True, exist_ok=True)
        rc, out, err, got = _transfer_push_once(
            ctx,
            abs_ditto=abs_ditto,
            abs_ssh=abs_ssh,
            abs_scp=abs_scp,
            ssh_target=ssh_target,
            local_home=local_home,
            remote_home=remote_home,
            rels=batch,
            sizes=bsz,
            dry_run=False,
            timeout=timeout,
            work=bwork,
            progress=prog,
            bytes_base=bytes_base + done,
            bytes_total=bytes_total or (bytes_base + payload),
            bind_ip=bind_ip,
            stream=stream,
        )
        if rc != 0:
            return rc, out, err or f"push batch {i}/{len(batches)} failed", done
        done += got
        if out:
            outs.append(out)
    return (
        0,
        f"push: {len(rels)} files ({format_bytes(payload)}); " + (outs[0] if outs else "ok"),
        "",
        done,
    )


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


def sync_home(
    ctx: AppContext,
    *,
    dry_run: bool = False,
    peer: str | None = None,
    peer_limit: int | None = None,
    push_only: bool = False,
    pull_only: bool = False,
    user: str | None = None,
    home: str | Path | None = None,
    remote_home: str | Path | None = None,
    extra_excludes: tuple[str, ...] = (),
    exclude_from: str | Path | None = None,
    presets: tuple[str, ...] = (),
    includes: tuple[str, ...] = (),
    full_home: bool = False,
    conflict_policy: str = "newer",
    compare_only: bool = False,
    safetynet: bool = False,
    verify: bool = False,
    verify_sample: int = 20,
    quick: bool = False,
    max_files: int | None = None,
    max_bytes: int | None = None,
    apfs_snapshot: bool = False,
    notify: bool = False,
    no_speedtest: bool = False,
    no_stream: bool = False,
    min_free_bytes: int | None = None,
    timeout: float = TIMEOUT_SYNC,
    skip_ssh_check: bool = False,
    progress: ProgressLike | None = None,
    write_log: bool = True,
    force_icloud: bool = False,
    identical: bool = False,
    icloud_timeout_per_file: float = 20.0,
    icloud_max_seconds: float = 900.0,
    target: str = "home",
    via: str = "tb",
    transport: str | None = None,
) -> SyncHomeResult:
    """
    Two-way tree sync via Apple ``ditto`` (metadata-complete) over SSH.
    ``target="dev"`` uses ``~/Developer`` as the tree (see ``resolve_sync_tree``).

    CCC-inspired options: compare, presets/includes, exclude-from, conflict
    policy, SafetyNet-lite, post-verify, quick update, batch limits, APFS
    snapshot, notifications, run history.

    ``force_icloud`` materializes iCloud dataless stubs (brctl + timed open)
    on local and peer before inventory. ``identical`` implies force_icloud,
    both directions, and post-verify for best-effort 1:1 (remaining cloud-only
    stubs are skipped and reported).
    """
    from maccluster.constants import (
        SYNC_CONFLICT_POLICIES,
        SYNC_QUICK_SLACK_S,
        SYNC_VERIFY_SAMPLE_DEFAULT,
    )
    from maccluster.services.sync_filters import (
        filter_inventory,
        load_exclude_file,
        merge_includes,
    )
    from maccluster.services.sync_history import (
        load_sync_state,
        save_sync_state,
        write_run_log,
    )
    from maccluster.services.sync_safetynet import backup_before_overwrite, new_run_dir
    from maccluster.services.sync_verify import verify_local_sample

    if push_only and pull_only:
        raise CliError("use only one of --push-only / --pull-only", exit_code=2)
    policy = (conflict_policy or "newer").strip().lower()
    if policy not in SYNC_CONFLICT_POLICIES:
        raise CliError(
            f"invalid --conflict-policy {conflict_policy!r}; "
            f"choose from {', '.join(sorted(SYNC_CONFLICT_POLICIES))}",
            exit_code=2,
        )
    target = normalize_sync_target(target) or "home"
    transport = sync_transport.normalize_transport(transport)
    via_n = "wifi" if transport == "wifi" else (via or "tb").strip().lower()
    if via_n not in ("tb", "wifi"):
        raise CliError(f"invalid sync via {via!r} (use tb or wifi)", exit_code=2)
    if compare_only:
        dry_run = True

    prog: ProgressLike = progress if progress is not None else NullProgress()
    t0 = time.monotonic()

    cfg, self_node = load_and_bind_self(ctx)
    try:
        from maccluster.services.keychain_service import resolve_ssh_user

        default_user = resolve_ssh_user(ctx, explicit=user)
    except Exception:
        default_user = (user or os.environ.get("USER") or getpass.getuser() or "").strip()
    if not default_user:
        raise CliError("cannot determine local username for SSH", exit_code=1)

    local_home = Path(home) if home else resolve_sync_tree(target, None)
    if not local_home.is_dir():
        label = "Developer dir" if target == "dev" else "local home"
        raise CliError(f"{label} is not a directory: {local_home}", exit_code=1)
    remote_home_path = str(Path(remote_home) if remote_home else local_home)

    try:
        abs_ditto = ctx.runner.resolve("ditto")
    except CliError as exc:
        raise CliError(
            "ditto not found (required Apple system tool in /usr/bin)", exit_code=1
        ) from exc
    try:
        abs_ssh = ctx.runner.resolve("ssh")
        abs_scp = ctx.runner.resolve("scp")
    except CliError as exc:
        raise CliError(f"ssh/scp not found: {exc.message}", exit_code=1) from exc

    # Filters
    from maccluster.config.paths import default_sync_exclude_file

    file_excludes = load_exclude_file(
        Path(exclude_from) if exclude_from else default_sync_exclude_file()
    )
    includes_resolved = merge_includes(presets, includes)
    # Bare `sync home` without scope hung for hours on Library/CloudStorage.
    # Default to high-value roots unless --full-home or explicit includes/presets.
    if target == "home" and not includes_resolved and not full_home:
        from maccluster.constants import SYNC_DEFAULT_PRESETS

        includes_resolved = merge_includes(SYNC_DEFAULT_PRESETS, ())
        prog.note(
            "default scope: "
            + ", ".join(includes_resolved)
            + "  (pass --full-home for entire $HOME, or --preset/--include)"
        )
    elif full_home and includes_resolved:
        raise CliError(
            "use either --full-home or --preset/--include, not both",
            exit_code=2,
        )
    extra_dev = SYNC_DEV_EXCLUDES if target == "dev" else ()
    excludes = tuple(SYNC_HOME_EXCLUDES) + extra_dev + file_excludes + tuple(extra_excludes)
    peers = _resolve_peers(
        cfg.nodes,
        self_node,
        peer_filter=peer,
        default_user=default_user,
        peer_limit=peer_limit,
    )
    if via_n == "wifi":
        no_speedtest = True
        mapped: list[tuple[Node, str]] = []
        for node, _tb_target in peers:
            wt = wifi_ssh_target(node, default_user=default_user)
            if wt is not None:
                mapped.append((node, wt))
        if not mapped:
            raise CliError(
                "wifi sync needs a .local hostname on the peer in cluster.toml",
                exit_code=1,
            )
        peers = mapped
        bind_ip = None  # default route / Wi-Fi — never TB BindAddress
    else:
        bind_ip = str(self_node.ip)  # TB bridge Self-IP only — never Wi‑Fi

    if identical:
        force_icloud = True
        verify = True
        if push_only or pull_only:
            raise CliError(
                "--identical requires both directions (omit --push-only / --pull-only)",
                exit_code=2,
            )
        prog.note("identical mode: force-icloud + bidirectional + verify (1:1)")

    if force_icloud and not dry_run and not compare_only:
        _run_force_icloud(
            ctx,
            local_home=local_home,
            peers=peers,
            abs_ssh=abs_ssh,
            abs_scp=abs_scp,
            bind_ip=bind_ip,
            timeout_per_file=icloud_timeout_per_file,
            max_seconds=icloud_max_seconds,
            prog=prog,
        )

    snap_label = _maybe_apfs_snapshot(ctx, enabled=apfs_snapshot and not dry_run)
    if snap_label:
        prog.note(f"APFS local snapshot: {snap_label}")

    if not no_speedtest and not compare_only:
        try:
            from maccluster.services.speedtest_service import (
                format_speedtest_report,
                run_speedtest,
            )

            st = run_speedtest(
                ctx,
                peer=peer,
                duration=3,
                skip_iperf=False,
                try_start_server=True,
            )
            prog.note(format_speedtest_report(st))
            if not st.good_enough:
                prog.note(
                    "warning: TB path below ideal (want 40 Gb/s cable; 20 Gb/s is minimum OK)"
                )
        except Exception as exc:
            prog.note(f"warning: speedtest preflight skipped: {exc}")

    free_local = _free_bytes(local_home)
    if min_free_bytes is not None and free_local is not None and free_local < min_free_bytes:
        raise CliError(
            f"local free space {format_bytes(free_local)} below --min-free "
            f"{format_bytes(min_free_bytes)}",
            exit_code=1,
        )

    state = load_sync_state()
    last_ts_ns = int(state.get("last_success_mtime_ns") or 0)
    if quick and last_ts_ns > 0:
        prog.note(
            f"quick update: prefer files newer than last success (slack {SYNC_QUICK_SLACK_S}s)"
        )

    local_inv: dict[str, FileMeta] | None = None
    peer_results: list[SyncPeerResult] = []
    sample_n = verify_sample if verify_sample > 0 else SYNC_VERIFY_SAMPLE_DEFAULT
    sn_run: Path | None = None

    for node, ssh_target in peers:
        bind_label = bind_ip or via_n
        prog.note(f"peer {node.id} ({node.ip}) {via_n} {ssh_target} bind={bind_label}")
        prog.phase("ssh", direction="", detail=f"{ssh_target} via {bind_label}")
        if not skip_ssh_check:
            fail = _preflight_ssh(ctx, abs_ssh, ssh_target, bind_ip=bind_ip)
            if fail is not None:
                fail_l = fail.lower()
                if "no route to host" in fail_l or "network is unreachable" in fail_l:
                    ssh_msg = (
                        f"peer unreachable on TB mesh ({node.ip}). "
                        f"Cable may be up but peer IP stack is down — on peer run: "
                        f"`sudo maccluster up` then `maccluster status`. "
                        f"detail: {fail}"
                    )
                elif "permission denied" in fail_l or "publickey" in fail_l:
                    ssh_msg = (
                        f"SSH login failed (BatchMode). Fix keys: "
                        f"ssh-copy-id {ssh_target} — see docs/PEER-SSH.md. "
                        f"detail: {fail}"
                    )
                else:
                    ssh_msg = f"SSH failed to {ssh_target} (bind {bind_ip}). detail: {fail}"
                peer_results.append(
                    SyncPeerResult(
                        peer_id=node.id,
                        peer_ip=str(node.ip),
                        ssh_target=ssh_target,
                        via=via_n,
                        push_rc=-1,
                        pull_rc=-1,
                        ok=False,
                        message=ssh_msg,
                        free_bytes_local=free_local,
                    )
                )
                prog.note(f"  FAIL SSH: {fail[:120]}")
                continue

        free_remote = _remote_free_bytes(
            ctx, abs_ssh, ssh_target, remote_home_path, bind_ip=bind_ip
        )
        if min_free_bytes is not None and free_remote is not None and free_remote < min_free_bytes:
            peer_results.append(
                SyncPeerResult(
                    peer_id=node.id,
                    peer_ip=str(node.ip),
                    ssh_target=ssh_target,
                    via=via_n,
                    push_rc=-1,
                    pull_rc=-1,
                    ok=False,
                    message=(
                        f"peer free space {format_bytes(free_remote)} below "
                        f"--min-free {format_bytes(min_free_bytes)}"
                    ),
                    free_bytes_local=free_local,
                    free_bytes_remote=free_remote,
                )
            )
            continue

        if local_inv is None:
            prog.phase("inventory", direction="local", detail=str(local_home))
            local_inv = inventory_local(
                local_home,
                excludes,
                includes_resolved,
                progress=prog,
                # Keep local inventory bounded; same default as remote script
                max_sec=min(240.0, max(60.0, timeout * 0.5)),
            )
            local_inv = filter_inventory(local_inv, includes_resolved)
            if quick and last_ts_ns > 0:
                cutoff = last_ts_ns - SYNC_QUICK_SLACK_S * 1_000_000_000
                # Keep recently touched + will still plan missing via remote side
                local_inv = {k: v for k, v in local_inv.items() if v.mtime_ns >= cutoff}
            prog.note(f"  local inventory: {len(local_inv)} files")

        with tempfile.TemporaryDirectory(prefix="maccluster-sync-") as tmp:
            work = Path(tmp)
            prog.phase("inventory", direction="remote", detail=ssh_target)
            prog.update(
                phase="inventory",
                direction="remote",
                detail=f"listing on {ssh_target}…",
                path=ssh_target,
                force=True,
            )
            remote_inv, inv_err, inv_complete = _remote_inventory(
                ctx,
                abs_ssh,
                abs_scp,
                ssh_target,
                remote_home_path,
                excludes,
                # Cap inventory so iCloud/FP hangs cannot block full --timeout hours.
                # Must exceed the remote MAX_SEC budget, else SSH kills the walk
                # before it can finish and every run plans against a partial list.
                timeout=min(timeout, 1200.0),
                work=work,
                bind_ip=bind_ip,
                includes=includes_resolved,
                include_dotdirs=(target == "dev"),
                safe_scandir=(target != "dev"),
            )
            if remote_inv is None:
                peer_results.append(
                    SyncPeerResult(
                        peer_id=node.id,
                        peer_ip=str(node.ip),
                        ssh_target=ssh_target,
                        via=via_n,
                        push_rc=-1,
                        pull_rc=-1,
                        ok=False,
                        message=f"remote inventory failed: {inv_err}",
                        free_bytes_local=free_local,
                        free_bytes_remote=free_remote,
                    )
                )
                prog.note(f"  FAIL inventory: {inv_err[:120]}")
                continue
            if inv_err:
                prog.note(f"  inventory note: {inv_err[:160]}")
            if not inv_complete:
                prog.note(
                    "  remote inventory INCOMPLETE — files it never reached are "
                    "treated as unknown, not missing; they are left for the next run"
                )

            remote_inv = filter_inventory(remote_inv, includes_resolved)
            prog.note(f"  remote inventory: {len(remote_inv)} files")
            # For quick mode we still need full remote for pull of new remote files
            # but local is reduced — re-walk missing remote-only is fine

            to_push, to_pull, plan_stats = plan_transfers(
                local_inv, remote_inv, policy=policy, remote_complete=inv_complete
            )
            if plan_stats.get("remote_unknown"):
                prog.note(
                    f"  {plan_stats['remote_unknown']} files skipped as unknown "
                    "(remote walk truncated) — raise MACCLUSTER_INV_MAX_SEC to cover the tree"
                )
            if push_only:
                to_pull = []
            if pull_only:
                to_push = []

            push_sizes = {r: local_inv[r].size for r in to_push if r in local_inv}
            # If quick dropped local files that remote needs, sizes only for known
            for r in to_push:
                if r not in push_sizes and r in local_inv:
                    push_sizes[r] = local_inv[r].size
            pull_sizes = {r: remote_inv[r].size for r in to_pull if r in remote_inv}

            to_push, to_pull, truncated = apply_batch_limits(
                to_push,
                to_pull,
                push_sizes,
                pull_sizes,
                max_files=max_files,
                max_bytes=max_bytes,
            )
            push_sizes = {r: push_sizes[r] for r in to_push if r in push_sizes}
            pull_sizes = {r: pull_sizes[r] for r in to_pull if r in pull_sizes}
            push_bytes = sum(push_sizes.values())
            pull_bytes = sum(pull_sizes.values())
            total_bytes = push_bytes + pull_bytes
            total_files = len(to_push) + len(to_pull)

            # Free-space headroom: need room for incoming pull on local / push on remote
            if not dry_run and free_local is not None and pull_bytes > free_local:
                peer_results.append(
                    SyncPeerResult(
                        peer_id=node.id,
                        peer_ip=str(node.ip),
                        ssh_target=ssh_target,
                        via=via_n,
                        push_rc=-1,
                        pull_rc=-1,
                        ok=False,
                        message=(
                            f"not enough local free space for pull "
                            f"({format_bytes(pull_bytes)} needed, "
                            f"{format_bytes(free_local)} free)"
                        ),
                        pull_files=len(to_pull),
                        pull_bytes=pull_bytes,
                        free_bytes_local=free_local,
                        free_bytes_remote=free_remote,
                        only_local=plan_stats.get("only_local", 0),
                        only_remote=plan_stats.get("only_remote", 0),
                        local_newer=plan_stats.get("local_newer", 0),
                        remote_newer=plan_stats.get("remote_newer", 0),
                        equal=plan_stats.get("equal", 0),
                        conflicts_skipped=plan_stats.get("conflicts_skipped", 0),
                    )
                )
                continue
            if not dry_run and free_remote is not None and push_bytes > free_remote:
                peer_results.append(
                    SyncPeerResult(
                        peer_id=node.id,
                        peer_ip=str(node.ip),
                        ssh_target=ssh_target,
                        via=via_n,
                        push_rc=-1,
                        pull_rc=-1,
                        ok=False,
                        message=(
                            f"not enough peer free space for push "
                            f"({format_bytes(push_bytes)} needed, "
                            f"{format_bytes(free_remote)} free)"
                        ),
                        push_files=len(to_push),
                        push_bytes=push_bytes,
                        free_bytes_local=free_local,
                        free_bytes_remote=free_remote,
                    )
                )
                continue

            prog.reset_timer()
            prog.set_totals(files=total_files, bytes_=total_bytes)
            mode = "compare" if compare_only else ("dry-run" if dry_run else "sync")
            prog.note(
                f"  {mode} plan [{policy}]: push {len(to_push)} "
                f"({format_bytes(push_bytes)}) · pull {len(to_pull)} "
                f"({format_bytes(pull_bytes)})" + (" [truncated]" if truncated else "")
            )
            if to_push and prog.enabled:
                for sample in to_push[:5]:
                    prog.note(f"    push + {sample} ({format_bytes(push_sizes.get(sample, 0))})")
                if len(to_push) > 5:
                    prog.note(f"    push … +{len(to_push) - 5} more")
            if to_pull and prog.enabled:
                for sample in to_pull[:5]:
                    prog.note(f"    pull + {sample} ({format_bytes(pull_sizes.get(sample, 0))})")
                if len(to_pull) > 5:
                    prog.note(f"    pull … +{len(to_pull) - 5} more")

            push_rc = pull_rc = 0
            push_out = pull_out = push_err = pull_err = ""
            messages: list[str] = []
            outcome = sync_transport.NO_TRANSFER
            t_peer = time.monotonic()
            sn_count = 0
            v_ok: bool | None = None
            v_checked = v_mis = 0

            if compare_only:
                pd = precise_delta(local_inv, remote_inv, policy=policy, sample=8)
                messages.append(
                    f"delta only_local={pd.only_local.count}/"
                    f"{format_bytes(pd.only_local.bytes)} "
                    f"only_remote={pd.only_remote.count}/"
                    f"{format_bytes(pd.only_remote.bytes)} "
                    f"local_newer={pd.local_newer.count}/"
                    f"{format_bytes(pd.local_newer.bytes)} "
                    f"remote_newer={pd.remote_newer.count}/"
                    f"{format_bytes(pd.remote_newer.bytes)} "
                    f"equal={pd.equal.count} "
                    f"plan_push={len(pd.to_push)}/{format_bytes(pd.push_bytes)} "
                    f"plan_pull={len(pd.to_pull)}/{format_bytes(pd.pull_bytes)}"
                )
                for line in format_precise_delta(pd, peer_id=node.id, peer_ip=str(node.ip)):
                    prog.note(f"  {line}")
                push_out = _sample_list(list(pd.to_push), label="would push")
                pull_out = _sample_list(list(pd.to_pull), label="would pull")
            else:
                if safetynet and not dry_run and not push_only and to_pull:
                    if sn_run is None:
                        sn_run = new_run_dir()
                    overwrite = [r for r in to_pull if r in local_inv]
                    sn_count = backup_before_overwrite(
                        local_home,
                        overwrite,
                        run_dir=sn_run,
                        abs_ditto=abs_ditto,
                        runner=ctx.runner,
                        timeout=timeout,
                    )
                    if sn_count:
                        prog.note(f"  SafetyNet: backed up {sn_count} files → {sn_run}")

                # Transport ladder (rdma → tb → wifi) lives in sync_transport.py
                wifi_tgt = wifi_ssh_target(node, default_user=default_user)
                tgt = sync_transport.TransferTarget(
                    node, ssh_target, bind_ip, wifi_tgt, local_home, remote_home_path
                )
                choice = sync_transport.select_transports(
                    tgt, ctx, via=via_n, priority=cfg.transport_priority, override=transport
                )
                outcome = sync_transport.run_transfer_ladder(
                    ctx,
                    choice=choice,
                    plan=sync_transport.TransferPlan(
                        to_push, to_pull, push_sizes, pull_sizes, local_inv, remote_inv, policy
                    ),
                    target=tgt,
                    dry_run=dry_run,
                    timeout=timeout,
                    work=work,
                    progress=prog,
                    stream=not no_stream,
                    push_only=push_only,
                    pull_only=pull_only,
                )
                push_rc, pull_rc = outcome.push_rc, outcome.pull_rc
                push_out, pull_out = outcome.push_stdout, outcome.pull_stdout
                push_err, pull_err = outcome.push_stderr, outcome.pull_stderr
                messages.extend(outcome.messages)

                if verify and not dry_run and pull_rc == 0 and to_pull:
                    # Expected meta from remote inventory for pulled files
                    expected = {r: remote_inv[r] for r in to_pull if r in remote_inv}
                    v_ok, v_checked, v_mis, bad = verify_local_sample(
                        local_home, expected, to_pull, sample=sample_n
                    )
                    if not v_ok:
                        messages.append(
                            f"verify FAIL {v_mis}/{v_checked} mismatches"
                            + (f" e.g. {bad[0]}" if bad else "")
                        )
                    else:
                        messages.append(f"verify OK ({v_checked} samples)")

            ok = push_rc == 0 and pull_rc == 0 and (v_ok is not False)
            elapsed = max(1e-6, time.monotonic() - t_peer)
            rate = (push_bytes + pull_bytes) / elapsed if not dry_run and not compare_only else 0.0
            if not messages:
                messages.append(
                    "compare ok" if compare_only else ("dry-run ok" if dry_run else "ok")
                )
            if total_files and not compare_only:
                messages.append(
                    f"{format_bytes(push_bytes + pull_bytes)} in {elapsed:.1f}s"
                    + (f" ({format_rate(rate)})" if rate > 0 else "")
                )
            if truncated:
                messages.append("batch limit — re-run for remainder")

            peer_results.append(
                SyncPeerResult(
                    peer_id=node.id,
                    peer_ip=str(node.ip),
                    ssh_target=ssh_target,
                    via=via_n,
                    push_rc=push_rc,
                    pull_rc=pull_rc,
                    push_stdout=push_out,
                    pull_stdout=pull_out,
                    push_stderr=push_err,
                    pull_stderr=pull_err,
                    ok=ok,
                    message="; ".join(messages),
                    push_files=len(to_push),
                    pull_files=len(to_pull),
                    push_bytes=push_bytes,
                    pull_bytes=pull_bytes,
                    only_local=plan_stats.get("only_local", 0),
                    only_remote=plan_stats.get("only_remote", 0),
                    local_newer=plan_stats.get("local_newer", 0),
                    remote_newer=plan_stats.get("remote_newer", 0),
                    equal=plan_stats.get("equal", 0),
                    conflicts_skipped=plan_stats.get("conflicts_skipped", 0),
                    sample_push=tuple(to_push[:15]),
                    sample_pull=tuple(to_pull[:15]),
                    verify_ok=v_ok,
                    verify_checked=v_checked,
                    verify_mismatches=v_mis,
                    safetynet_backed_up=sn_count,
                    free_bytes_local=free_local,
                    free_bytes_remote=free_remote,
                    truncated=truncated,
                    transport=outcome.transport,
                    downgrades=outcome.downgrades,
                )
            )
            status = "OK" if ok else "FAIL"
            prog.note(f"  [{status}] {node.id} in {elapsed:.1f}s")

    total_elapsed = time.monotonic() - t0
    prog.finish(f"sync finished in {total_elapsed:.1f}s")

    if compare_only:
        strategy = f"compare ({policy})"
    elif identical:
        strategy = f"identical/1:1 ({policy}, force-icloud, Apple ditto)"
    elif force_icloud:
        strategy = f"{policy} (Apple ditto, force-icloud)"
    else:
        strategy = f"{policy} (Apple ditto)"
    result = SyncHomeResult(
        local_home=str(local_home),
        dry_run=dry_run,
        strategy=strategy,
        peers=tuple(peer_results),
        excludes=excludes,
        includes=includes_resolved,
        conflict_policy=policy,
        compare_only=compare_only,
        safetynet=safetynet,
        verify=verify,
        quick=quick,
        apfs_snapshot=snap_label,
        max_files=max_files,
        max_bytes=max_bytes,
        target=target,
        wifi_repos=includes_resolved if via_n == "wifi" else (),
        transport_priority=tuple(cfg.transport_priority),
    )

    log_path: str | None = None
    if write_log and not compare_only:
        try:
            log_path = str(write_run_log(result))
        except OSError as exc:
            prog.note(f"warning: could not write sync log: {exc}")
    if log_path:
        result = replace(result, log_path=log_path)

    if result.ok and not dry_run and not compare_only:
        # Advance quick-update watermark
        now_ns = time.time_ns()
        st = load_sync_state()
        st["last_success_mtime_ns"] = now_ns
        st["last_success_ts"] = time.time()
        save_sync_state(st)

    if notify and not result.ok:
        fails = [p.peer_id for p in result.peers if not p.ok]
        _notify_fail(
            ctx,
            "MacCluster sync failed",
            f"peers: {', '.join(fails) or 'unknown'}",
        )

    return result


def exit_code_for_sync(result: SyncHomeResult) -> int:
    if not result.peers:
        return 2
    if all(p.ok for p in result.peers):
        return 0
    if any(p.ok for p in result.peers):
        return 3
    return 1
