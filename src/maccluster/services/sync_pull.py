"""Pull leg of the home sync: peer stages a ditto CPIO archive, we fetch it.

Extracted verbatim from ``sync_service``. ``_transfer_pull`` is the entry
point (large files via direct scp, the rest in size-capped CPIO batches);
``_REMOTE_STAGE_PY`` is the helper script run on the peer.
"""

from __future__ import annotations

import os
import shlex
from pathlib import Path

from maccluster.app_factory import AppContext
from maccluster.render.progress import NullProgress, ProgressLike, format_bytes
from maccluster.services.sync_plan import _chunk_rels, _sample_list, _split_large_files
from maccluster.services.sync_ssh import _scp_argv, _scp_one_file, _ssh_argv

_REMOTE_STAGE_PY = 'import os, stat, subprocess, sys\n\nhome, list_path, stage, archive = sys.argv[1:5]\nos.makedirs(stage, exist_ok=True)\nUF_DATALESS = 0x40000000\nn = 0\nskipped = 0\nwith open(list_path, encoding="utf-8") as fh:\n    for line in fh:\n        rel = line.strip()\n        if not rel or ".." in rel.split("/"):\n            continue\n        src = os.path.join(home, rel)\n        dst = os.path.join(stage, rel)\n        if not os.path.lexists(src):\n            skipped += 1\n            continue\n        try:\n            st = os.lstat(src)\n        except OSError:\n            skipped += 1\n            continue\n        if getattr(st, "st_flags", 0) & UF_DATALESS:\n            skipped += 1\n            continue\n        if not (stat.S_ISREG(st.st_mode) or stat.S_ISLNK(st.st_mode)):\n            skipped += 1\n            continue\n        # Unreadable dataless-ish edge cases\n        if not os.access(src, os.R_OK) and not stat.S_ISLNK(st.st_mode):\n            skipped += 1\n            continue\n        os.makedirs(os.path.dirname(dst) or ".", exist_ok=True)\n        if os.path.lexists(dst):\n            try:\n                os.unlink(dst)\n            except OSError:\n                pass\n        ok = False\n        try:\n            os.link(src, dst)\n            ok = True\n        except OSError:\n            try:\n                r = subprocess.run(\n                    ["/bin/cp", "-p", src, dst],\n                    stdout=subprocess.DEVNULL,\n                    stderr=subprocess.DEVNULL,\n                    timeout=30,\n                    check=False,\n                )\n                ok = r.returncode == 0 and os.path.lexists(dst)\n            except Exception:\n                ok = False\n        if ok:\n            n += 1\n        else:\n            skipped += 1\n\nif n == 0:\n    print("staged=0 skipped=%d archive_rc=0" % skipped, flush=True)\n    open(archive, "wb").close()\n    sys.exit(0)\n\nrc = 1\ntry:\n    rc = subprocess.run(\n        ["/usr/bin/ditto", "-c", stage, archive],\n        timeout=max(120, min(3600, n // 10 + 60)),\n        check=False,\n    ).returncode\nexcept Exception:\n    rc = 1\n\narch_ok = os.path.isfile(archive) and os.path.getsize(archive) > 0\nif rc != 0 and arch_ok:\n    rc = 0\nprint("staged=%d skipped=%d archive_rc=%d" % (n, skipped, rc), flush=True)\n# Soft-ok empty transfer only when nothing staged; never claim success with missing archive\nif n > 0 and not arch_ok:\n    sys.exit(1)\nsys.exit(0 if arch_ok or n == 0 else rc)\n'


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
