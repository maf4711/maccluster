"""Push leg of the home sync: hardlink-stage locally, ditto CPIO to the peer.

Extracted verbatim from ``sync_service``. ``_transfer_push`` is the entry
point (large files via direct scp, the rest streamed or copied as size-capped
CPIO batches); ``_stage_hardlinks`` builds the staging tree.
"""

from __future__ import annotations

import os
import shlex
import shutil
from pathlib import Path

from maccluster.app_factory import AppContext
from maccluster.render.progress import NullProgress, ProgressLike, format_bytes
from maccluster.services.sync_plan import _chunk_rels, _sample_list, _split_large_files
from maccluster.services.sync_ssh import _scp_argv, _scp_one_file, _ssh_argv


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
