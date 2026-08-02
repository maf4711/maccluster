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
import stat
import tempfile
import time
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path

from maccluster.app_factory import AppContext
from maccluster.constants import (
    SYNC_HOME_EXCLUDES,
    TIMEOUT_SSH,
    TIMEOUT_SYNC,
)
from maccluster.domain.models import Node, SyncHomeResult, SyncPeerResult
from maccluster.errors import CliError
from maccluster.render.progress import NullProgress, ProgressLike, format_bytes, format_rate
from maccluster.services.config_service import load_and_bind_self

# Remote inventory: argv home excludes_file → lines relpath\\tmtime_ns\\tsize
_REMOTE_INVENTORY_PY = """\
import fnmatch, os, stat, sys
root, ex_path = sys.argv[1], sys.argv[2]
ex = open(ex_path, encoding="utf-8").read().splitlines() if os.path.isfile(ex_path) else []

def excl(rel):
    rel = rel.replace("\\\\", "/")
    while rel.startswith("./"):
        rel = rel[2:]
    rel = rel.lstrip("/")
    parts = rel.split("/")
    for pat in ex:
        if not pat:
            continue
        p = pat.replace("\\\\", "/")
        if p.endswith("/"):
            b = p.rstrip("/")
            if rel == b or rel.startswith(b + "/"):
                return True
            if b.startswith("**/") and (
                b[3:] in parts or any(fnmatch.fnmatch(x, b[3:]) for x in parts)
            ):
                return True
        elif p.startswith("**/"):
            rest = p[3:]
            if any(x == rest or fnmatch.fnmatch(x, rest) for x in parts):
                return True
            if fnmatch.fnmatch(rel, p) or fnmatch.fnmatch(os.path.basename(rel), rest):
                return True
        else:
            if fnmatch.fnmatch(rel, p) or fnmatch.fnmatch(os.path.basename(rel), p):
                return True
            b = p.rstrip("/")
            if rel == b or rel.startswith(b + "/"):
                return True
    return False

for dp, dns, fns in os.walk(root):
    rel_d = os.path.relpath(dp, root)
    if rel_d == ".":
        rel_d = ""
    keep = []
    for d in dns:
        r = (rel_d + "/" + d) if rel_d else d
        if not excl(r) and not excl(r + "/"):
            keep.append(d)
    dns[:] = keep
    for f in fns:
        r = ((rel_d + "/" + f) if rel_d else f).replace("\\\\", "/")
        if excl(r):
            continue
        p = os.path.join(dp, f)
        try:
            st = os.lstat(p)
        except OSError:
            continue
        if not (stat.S_ISREG(st.st_mode) or stat.S_ISLNK(st.st_mode)):
            continue
        print(f"{r}\\t{st.st_mtime_ns}\\t{st.st_size}")
"""

# Remote stage listed paths (hardlink) + ditto -c
_REMOTE_STAGE_PY = """\
import os, subprocess, sys
home, list_path, stage, archive = sys.argv[1:5]
os.makedirs(stage, exist_ok=True)
n = 0
with open(list_path, encoding="utf-8") as fh:
    for line in fh:
        rel = line.strip()
        if not rel or ".." in rel.split("/"):
            continue
        src = os.path.join(home, rel)
        dst = os.path.join(stage, rel)
        if not os.path.lexists(src):
            continue
        os.makedirs(os.path.dirname(dst) or ".", exist_ok=True)
        if os.path.lexists(dst):
            try:
                os.unlink(dst)
            except OSError:
                pass
        try:
            os.link(src, dst)
        except OSError:
            if os.path.islink(src) or os.path.isfile(src):
                subprocess.run(["/usr/bin/ditto", src, dst], check=False)
            else:
                continue
        n += 1
rc = subprocess.run(["/usr/bin/ditto", "-c", stage, archive]).returncode
print(f"staged={n} archive_rc={rc}")
sys.exit(rc)
"""


@dataclass(frozen=True)
class FileMeta:
    mtime_ns: int
    size: int


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
    return peers


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


def _norm_rel(rel: str) -> str:
    rel = rel.replace("\\", "/")
    while rel.startswith("./"):
        rel = rel[2:]
    return rel.lstrip("/")


def is_excluded(rel: str, patterns: tuple[str, ...]) -> bool:
    """Match relative path against rsync-like exclude patterns."""
    rel = _norm_rel(rel)
    parts = rel.split("/")
    for pat in patterns:
        if not pat:
            continue
        p = pat.replace("\\", "/")
        if p.endswith("/"):
            base = p.rstrip("/")
            if rel == base or rel.startswith(base + "/"):
                return True
            if base.startswith("**/"):
                name = base[3:]
                if name in parts or any(fnmatch(x, name) for x in parts):
                    return True
            elif "/" not in base and base in parts:
                return True
        elif p.startswith("**/"):
            rest = p[3:]
            if any(fnmatch(x, rest) or x == rest for x in parts):
                return True
            if fnmatch(rel, p) or fnmatch(Path(rel).name, rest):
                return True
        else:
            if fnmatch(rel, p) or fnmatch(Path(rel).name, p):
                return True
            base = p.rstrip("/")
            if rel == base or rel.startswith(base + "/"):
                return True
    return False


def inventory_local(root: Path, excludes: tuple[str, ...]) -> dict[str, FileMeta]:
    """Walk home; regular files + symlinks only."""
    out: dict[str, FileMeta] = {}
    root = root.resolve()
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        rel_dir = os.path.relpath(dirpath, root)
        if rel_dir == ".":
            rel_dir = ""
        keep: list[str] = []
        for d in dirnames:
            rel = f"{rel_dir}/{d}" if rel_dir else d
            rel = rel.replace("\\", "/")
            if is_excluded(rel, excludes) or is_excluded(rel + "/", excludes):
                continue
            keep.append(d)
        dirnames[:] = keep
        for name in filenames:
            rel = f"{rel_dir}/{name}" if rel_dir else name
            rel = rel.replace("\\", "/")
            if is_excluded(rel, excludes):
                continue
            path = Path(dirpath) / name
            try:
                st = path.lstat()
            except OSError:
                continue
            if not (stat.S_ISREG(st.st_mode) or stat.S_ISLNK(st.st_mode)):
                continue
            out[rel] = FileMeta(mtime_ns=st.st_mtime_ns, size=st.st_size)
    return out


def parse_inventory_text(text: str) -> dict[str, FileMeta]:
    out: dict[str, FileMeta] = {}
    for line in text.splitlines():
        if not line.strip() or "\t" not in line:
            continue
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        rel, mtime_s, size_s = parts[0], parts[1], parts[2]
        if ".." in rel.split("/"):
            continue
        try:
            out[rel] = FileMeta(mtime_ns=int(mtime_s), size=int(size_s))
        except ValueError:
            continue
    return out


def plan_transfers(
    local: dict[str, FileMeta],
    remote: dict[str, FileMeta],
) -> tuple[list[str], list[str]]:
    """Return (to_push, to_pull) — strict newer mtime wins; equal → skip."""
    to_push: list[str] = []
    to_pull: list[str] = []
    for rel, lm in local.items():
        rm = remote.get(rel)
        if rm is None or lm.mtime_ns > rm.mtime_ns:
            to_push.append(rel)
    for rel, rm in remote.items():
        lm = local.get(rel)
        if lm is None or rm.mtime_ns > lm.mtime_ns:
            to_pull.append(rel)
    to_push.sort()
    to_pull.sort()
    return to_push, to_pull


def _bytes_for_rels(inv: dict[str, FileMeta], rels: list[str]) -> int:
    return sum(inv[r].size for r in rels if r in inv)


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


def _sample_list(rels: list[str], *, label: str) -> str:
    sample = "\n".join(f"  + {r}" for r in rels[:30])
    more = f"\n  … +{len(rels) - 30} more" if len(rels) > 30 else ""
    return f"{label}: {len(rels)} files\n{sample}{more}"


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
) -> tuple[int, str, str, int]:
    """Returns (rc, stdout, stderr, bytes_transferred_estimate)."""
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
            "/usr/bin/python3",
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
    if stage_r.returncode != 0:
        return stage_r.returncode, out, (stage_r.stderr or "remote stage failed")[:500], 0

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
    got = local_arch.stat().st_size if local_arch.is_file() else arch_size
    on_pull_chunk(got, got if got else arch_size)

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


def _remote_inventory(
    ctx: AppContext,
    abs_ssh: str,
    abs_scp: str,
    ssh_target: str,
    remote_home: str,
    excludes: tuple[str, ...],
    *,
    timeout: float,
    work: Path,
    bind_ip: str | None = None,
) -> tuple[dict[str, FileMeta] | None, str]:
    script = work / "remote_inv.py"
    script.write_text(_REMOTE_INVENTORY_PY, encoding="utf-8")
    excl_file = work / "excludes.txt"
    excl_file.write_text("\n".join(excludes) + "\n", encoding="utf-8")
    remote_script = f"/tmp/maccluster-inv-{os.getpid()}.py"
    remote_excl = f"/tmp/maccluster-excl-{os.getpid()}.txt"

    for local, remote in ((script, remote_script), (excl_file, remote_excl)):
        scp = ctx.runner.run(
            _scp_argv(abs_scp, str(local), f"{ssh_target}:{remote}", bind_ip=bind_ip),
            timeout=min(timeout, 60.0),
        )
        if scp.returncode != 0:
            return None, (scp.stderr or f"scp {local.name} failed")[:300]

    r = ctx.runner.run(
        _ssh_argv(
            abs_ssh,
            ssh_target,
            "/usr/bin/python3",
            remote_script,
            remote_home,
            remote_excl,
            bind_ip=bind_ip,
        ),
        timeout=timeout,
    )
    ctx.runner.run(
        _ssh_argv(
            abs_ssh,
            ssh_target,
            "/bin/rm",
            "-f",
            remote_script,
            remote_excl,
            bind_ip=bind_ip,
        ),
        timeout=30.0,
    )
    if r.returncode != 0:
        return None, (r.stderr or r.stdout or "remote inventory failed")[:300]
    return parse_inventory_text(r.stdout or ""), ""


def sync_home(
    ctx: AppContext,
    *,
    dry_run: bool = False,
    peer: str | None = None,
    push_only: bool = False,
    pull_only: bool = False,
    user: str | None = None,
    home: str | Path | None = None,
    remote_home: str | Path | None = None,
    extra_excludes: tuple[str, ...] = (),
    timeout: float = TIMEOUT_SYNC,
    skip_ssh_check: bool = False,
    progress: ProgressLike | None = None,
) -> SyncHomeResult:
    """
    Two-way Home sync via Apple ``ditto`` (metadata-complete) over SSH.

    Strategy: newest-wins by mtime (no deletes). Per peer: inventory → push
    newer local files → pull newer remote files.
    """
    if push_only and pull_only:
        raise CliError("use only one of --push-only / --pull-only", exit_code=2)

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

    local_home = Path(home) if home else Path.home()
    if not local_home.is_dir():
        raise CliError(f"local home is not a directory: {local_home}", exit_code=1)
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

    excludes = tuple(SYNC_HOME_EXCLUDES) + tuple(extra_excludes)
    peers = _resolve_peers(cfg.nodes, self_node, peer_filter=peer, default_user=default_user)
    bind_ip = str(self_node.ip)  # TB bridge Self-IP only — never Wi‑Fi

    # Startup: TB cable grade + short speedtest (non-fatal)
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
            prog.note("warning: TB path below ideal (want 40 Gb/s cable; 20 Gb/s is minimum OK)")
    except Exception as exc:
        prog.note(f"warning: speedtest preflight skipped: {exc}")

    # Lazy: only walk $HOME after at least one peer passes SSH (homes are huge).
    local_inv: dict[str, FileMeta] | None = None
    peer_results: list[SyncPeerResult] = []

    for node, ssh_target in peers:
        prog.note(f"peer {node.id} ({node.ip}) via {ssh_target} bind={bind_ip}")
        prog.phase("ssh", direction="", detail=f"{ssh_target} via {bind_ip}")
        if not skip_ssh_check:
            fail = _preflight_ssh(ctx, abs_ssh, ssh_target, bind_ip=bind_ip)
            if fail is not None:
                peer_results.append(
                    SyncPeerResult(
                        peer_id=node.id,
                        peer_ip=str(node.ip),
                        ssh_target=ssh_target,
                        push_rc=-1,
                        pull_rc=-1,
                        ok=False,
                        message=(
                            f"SSH login failed (BatchMode). Fix keys: "
                            f"ssh-copy-id {ssh_target} — see docs/PEER-SSH.md. "
                            f"detail: {fail}"
                        ),
                    )
                )
                prog.note(f"  FAIL SSH: {fail[:120]}")
                continue

        if local_inv is None:
            prog.phase("inventory", direction="local", detail=str(local_home))
            local_inv = inventory_local(local_home, excludes)
            prog.note(f"  local inventory: {len(local_inv)} files")

        with tempfile.TemporaryDirectory(prefix="maccluster-sync-") as tmp:
            work = Path(tmp)
            prog.phase("inventory", direction="remote", detail=ssh_target)
            remote_inv, inv_err = _remote_inventory(
                ctx,
                abs_ssh,
                abs_scp,
                ssh_target,
                remote_home_path,
                excludes,
                timeout=timeout,
                work=work,
                bind_ip=bind_ip,
            )
            if remote_inv is None:
                peer_results.append(
                    SyncPeerResult(
                        peer_id=node.id,
                        peer_ip=str(node.ip),
                        ssh_target=ssh_target,
                        push_rc=-1,
                        pull_rc=-1,
                        ok=False,
                        message=f"remote inventory failed: {inv_err}",
                    )
                )
                prog.note(f"  FAIL inventory: {inv_err[:120]}")
                continue

            to_push, to_pull = plan_transfers(local_inv, remote_inv)
            if push_only:
                to_pull = []
            if pull_only:
                to_push = []

            push_sizes = {r: local_inv[r].size for r in to_push if r in local_inv}
            pull_sizes = {r: remote_inv[r].size for r in to_pull if r in remote_inv}
            push_bytes = sum(push_sizes.values())
            pull_bytes = sum(pull_sizes.values())
            total_bytes = push_bytes + pull_bytes
            total_files = len(to_push) + len(to_pull)

            prog.reset_timer()
            prog.set_totals(files=total_files, bytes_=total_bytes)
            prog.note(
                f"  plan: push {len(to_push)} files ({format_bytes(push_bytes)}) · "
                f"pull {len(to_pull)} files ({format_bytes(pull_bytes)})"
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
            done_bytes = 0
            t_peer = time.monotonic()

            if not pull_only:
                push_rc, push_out, push_err, _pb = _transfer_push(
                    ctx,
                    abs_ditto=abs_ditto,
                    abs_ssh=abs_ssh,
                    abs_scp=abs_scp,
                    ssh_target=ssh_target,
                    local_home=local_home,
                    remote_home=remote_home_path,
                    rels=to_push,
                    sizes=push_sizes,
                    dry_run=dry_run,
                    timeout=timeout,
                    work=work,
                    progress=prog,
                    bytes_base=0,
                    bytes_total=total_bytes,
                    bind_ip=bind_ip,
                )
                done_bytes = push_bytes
                if push_rc != 0:
                    messages.append(f"push failed rc={push_rc}")
                elif push_out:
                    messages.append(push_out.split("\n", 1)[0])

            if not push_only:
                pull_rc, pull_out, pull_err, _plb = _transfer_pull(
                    ctx,
                    abs_ditto=abs_ditto,
                    abs_ssh=abs_ssh,
                    abs_scp=abs_scp,
                    ssh_target=ssh_target,
                    local_home=local_home,
                    remote_home=remote_home_path,
                    rels=to_pull,
                    sizes=pull_sizes,
                    dry_run=dry_run,
                    timeout=timeout,
                    work=work,
                    progress=prog,
                    bytes_base=done_bytes,
                    bytes_total=total_bytes,
                    bind_ip=bind_ip,
                )
                if pull_rc != 0:
                    messages.append(f"pull failed rc={pull_rc}")
                elif pull_out:
                    messages.append(pull_out.split("\n", 1)[0])

            ok = push_rc == 0 and pull_rc == 0
            elapsed = max(1e-6, time.monotonic() - t_peer)
            rate = (push_bytes + pull_bytes) / elapsed if not dry_run else 0.0
            if not messages:
                messages.append("ok" if not dry_run else "dry-run ok")
            if total_files:
                messages.append(
                    f"{format_bytes(push_bytes + pull_bytes)} in {elapsed:.1f}s"
                    + (f" ({format_rate(rate)})" if rate > 0 else "")
                )

            peer_results.append(
                SyncPeerResult(
                    peer_id=node.id,
                    peer_ip=str(node.ip),
                    ssh_target=ssh_target,
                    push_rc=push_rc,
                    pull_rc=pull_rc,
                    push_stdout=push_out,
                    pull_stdout=pull_out,
                    push_stderr=push_err,
                    pull_stderr=pull_err,
                    ok=ok,
                    message="; ".join(messages),
                )
            )
            status = "OK" if ok else "FAIL"
            prog.note(f"  [{status}] {node.id} in {elapsed:.1f}s")

    total_elapsed = time.monotonic() - t0
    prog.finish(f"sync finished in {total_elapsed:.1f}s")
    return SyncHomeResult(
        local_home=str(local_home),
        dry_run=dry_run,
        strategy="newest-wins (Apple ditto)",
        peers=tuple(peer_results),
        excludes=excludes,
    )


def exit_code_for_sync(result: SyncHomeResult) -> int:
    if not result.peers:
        return 2
    if all(p.ok for p in result.peers):
        return 0
    if any(p.ok for p in result.peers):
        return 3
    return 1
