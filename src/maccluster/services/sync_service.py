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
from dataclasses import dataclass, replace
from fnmatch import fnmatch
from pathlib import Path

from maccluster.app_factory import AppContext
from maccluster.constants import (
    DEVELOPER_DIR_NAME,
    SYNC_DEV_EXCLUDES,
    SYNC_HOME_EXCLUDES,
    TIMEOUT_SSH,
    TIMEOUT_SYNC,
)
from maccluster.domain.models import Node, SyncHomeResult, SyncPeerResult
from maccluster.errors import CliError
from maccluster.render.progress import NullProgress, ProgressLike, format_bytes, format_rate
from maccluster.services.config_service import load_and_bind_self
from maccluster.services.sync_wifi import wifi_ssh_target

# Remote inventory: argv home excludes_file → lines relpath\\tmtime_ns\\tsize
_REMOTE_INVENTORY_PY = 'import fnmatch, json, os, signal, stat, subprocess, sys, time\n\n# Unbuffered inventory lines (SSH non-TTY otherwise loses stdout on kill/timeout)\ntry:\n    sys.stdout.reconfigure(line_buffering=True)\nexcept Exception:\n    pass\ntry:\n    sys.stderr.reconfigure(line_buffering=True)\nexcept Exception:\n    pass\n\nroot, ex_path = sys.argv[1], sys.argv[2]\nincludes = [x.strip().strip("/") for x in sys.argv[3:] if x.strip()]\nex = open(ex_path, encoding="utf-8").read().splitlines() if os.path.isfile(ex_path) else []\nPREF = ("Developer", "Downloads", ".ssh", ".config", "Desktop", "Documents")\nincludes.sort(key=lambda x: PREF.index(x.split("/")[0]) if x.split("/")[0] in PREF else 99)\nt0 = time.time()\nMAX_SEC = float(os.environ.get("MACCLUSTER_INV_MAX_SEC", "900"))\nDIR_SEC = float(os.environ.get("MACCLUSTER_INV_DIR_SEC", "6"))\nSKIP_NAMES = {\n    "imessage_export", "node_modules", ".git", "DerivedData",\n    "__pycache__", ".venv", "venv", ".Trash", "Library",\n}\nDOTDIRS = os.environ.get("MACCLUSTER_INV_DOTDIRS", "").strip().lower() in ("1", "true", "yes")\n# Per-directory child processes cost one interpreter start per folder: measured\n# 167 files/s, so a 4.4M-file tree needs 7h and always trips MAX_SEC. Only cloud\n# providers (iCloud/FileProvider) can wedge scandir, so the guard is opt-in.\nSAFE_SCANDIR = os.environ.get("MACCLUSTER_INV_SAFE_SCANDIR", "").strip().lower() in ("1", "true", "yes")\nif DOTDIRS:\n    SKIP_NAMES.discard(".git")\nUF_DATALESS = 0x40000000\nn_emitted = 0\n\n\ndef excl(rel):\n    rel = rel.replace("\\\\", "/").lstrip("./")\n    parts = rel.split("/")\n    for pat in ex:\n        if not pat:\n            continue\n        p = pat.replace("\\\\", "/")\n        if p.endswith("/"):\n            b = p.rstrip("/")\n            if rel == b or rel.startswith(b + "/"):\n                return True\n            if b.startswith("**/") and (b[3:] in parts or any(fnmatch.fnmatch(x, b[3:]) for x in parts)):\n                return True\n        elif p.startswith("**/"):\n            rest = p[3:]\n            if any(x == rest or fnmatch.fnmatch(x, rest) for x in parts):\n                return True\n            if fnmatch.fnmatch(rel, p) or fnmatch.fnmatch(os.path.basename(rel), rest):\n                return True\n        else:\n            if fnmatch.fnmatch(rel, p) or fnmatch.fnmatch(os.path.basename(rel), p):\n                return True\n            b = p.rstrip("/")\n            if rel == b or rel.startswith(b + "/"):\n                return True\n    return False\n\n\ndef safe_scandir(path):\n    """List dir. Fast in-process scandir; killable child only when asked."""\n    if not SAFE_SCANDIR:\n        try:\n            out = []\n            for e in os.scandir(path):\n                try:\n                    out.append([e.name, e.path, e.is_dir(follow_symlinks=False), e.is_file(follow_symlinks=False)])\n                except OSError:\n                    pass\n            return out\n        except OSError:\n            return None\n    code = (\n        "import os,json,sys\\n"\n        "p=sys.argv[1]\\n"\n        "o=[]\\n"\n        "try:\\n"\n        "  for e in os.scandir(p):\\n"\n        "    try:\\n"\n        "      o.append([e.name,e.path,e.is_dir(follow_symlinks=False),e.is_file(follow_symlinks=False)])\\n"\n        "    except OSError:\\n"\n        "      pass\\n"\n        "except Exception:\\n"\n        "  sys.exit(2)\\n"\n        "print(json.dumps(o))\\n"\n    )\n    try:\n        r = subprocess.run(\n            [sys.executable, "-c", code, path],\n            capture_output=True,\n            text=True,\n            timeout=DIR_SEC,\n        )\n    except subprocess.TimeoutExpired:\n        print("# skip-hang %s" % path, file=sys.stderr, flush=True)\n        return None\n    if r.returncode != 0:\n        return None\n    try:\n        return json.loads(r.stdout or "[]")\n    except Exception:\n        return None\n\n\ndef emit_file(home, path):\n    global n_emitted\n    try:\n        st = os.lstat(path)\n    except OSError:\n        return False\n    if not (stat.S_ISREG(st.st_mode) or stat.S_ISLNK(st.st_mode)):\n        return False\n    if getattr(st, "st_flags", 0) & UF_DATALESS:\n        return False\n    rel = os.path.relpath(path, home).replace("\\\\", "/")\n    if excl(rel):\n        return False\n    sys.stdout.write("%s\\t%d\\t%d\\n" % (rel, st.st_mtime_ns, st.st_size))\n    n_emitted += 1\n    if n_emitted % 200 == 0:\n        sys.stdout.flush()\n    return True\n\n\ndef walk_safe(home, start):\n    n = 0\n    stack = [start]\n    while stack:\n        if time.time() - t0 > MAX_SEC:\n            print("# inventory time budget", file=sys.stderr, flush=True)\n            break\n        cur = stack.pop()\n        entries = safe_scandir(cur)\n        if entries is None:\n            try:\n                label = os.path.relpath(cur, home)\n            except Exception:\n                label = cur\n            print("# skip-hang %s" % label, file=sys.stderr, flush=True)\n            continue\n        for name, path, is_dir, is_file in entries:\n            if time.time() - t0 > MAX_SEC:\n                break\n            if name in SKIP_NAMES:\n                continue\n            if name == ".DS_Store":\n                continue\n            # skip heavy/hidden dirs except .ssh / .config (DOTDIRS walks .git/.github)\n            if (not DOTDIRS) and name.startswith(".") and name not in (".ssh", ".config"):\n                if is_dir:\n                    continue\n            if is_dir:\n                rel = os.path.relpath(path, home).replace("\\\\", "/")\n                if excl(rel) or excl(rel + "/"):\n                    continue\n                stack.append(path)\n            elif is_file or True:\n                if emit_file(home, path):\n                    n += 1\n                    if n % 20000 == 0:\n                        print("# listed %d" % n, file=sys.stderr, flush=True)\n    return n\n\n\nwalk_roots = []\nif includes:\n    for inc in includes:\n        if not inc or ".." in inc.split("/"):\n            continue\n        p0 = os.path.join(root, inc)\n        if not os.path.lexists(p0):\n            continue\n        base = inc.split("/")[0]\n        if base in ("Documents", "Desktop") and "/" not in inc.rstrip("/"):\n            kids = safe_scandir(p0)\n            if kids is None:\n                print("# skip-hang %s" % inc, file=sys.stderr, flush=True)\n                continue\n            for name, path, is_dir, is_file in kids:\n                if name in SKIP_NAMES or name == ".DS_Store":\n                    continue\n                if is_dir:\n                    walk_roots.append((path, "%s/%s" % (inc.rstrip("/"), name)))\n                elif is_file:\n                    emit_file(root, path)\n        else:\n            walk_roots.append((p0, inc))\nelse:\n    walk_roots.append((root, ""))\n\nn = 0\nfor walk_root, label in walk_roots:\n    if time.time() - t0 > MAX_SEC:\n        break\n    print("# walk %s" % label, file=sys.stderr, flush=True)\n    n += walk_safe(root, walk_root)\n\nsys.stdout.flush()\nprint("# inventory done n=%d sec=%d" % (n_emitted, int(time.time() - t0)), file=sys.stderr, flush=True)\nsys.exit(0)\n'

_REMOTE_STAGE_PY = 'import os, stat, subprocess, sys\n\nhome, list_path, stage, archive = sys.argv[1:5]\nos.makedirs(stage, exist_ok=True)\nUF_DATALESS = 0x40000000\nn = 0\nskipped = 0\nwith open(list_path, encoding="utf-8") as fh:\n    for line in fh:\n        rel = line.strip()\n        if not rel or ".." in rel.split("/"):\n            continue\n        src = os.path.join(home, rel)\n        dst = os.path.join(stage, rel)\n        if not os.path.lexists(src):\n            skipped += 1\n            continue\n        try:\n            st = os.lstat(src)\n        except OSError:\n            skipped += 1\n            continue\n        if getattr(st, "st_flags", 0) & UF_DATALESS:\n            skipped += 1\n            continue\n        if not (stat.S_ISREG(st.st_mode) or stat.S_ISLNK(st.st_mode)):\n            skipped += 1\n            continue\n        # Unreadable dataless-ish edge cases\n        if not os.access(src, os.R_OK) and not stat.S_ISLNK(st.st_mode):\n            skipped += 1\n            continue\n        os.makedirs(os.path.dirname(dst) or ".", exist_ok=True)\n        if os.path.lexists(dst):\n            try:\n                os.unlink(dst)\n            except OSError:\n                pass\n        ok = False\n        try:\n            os.link(src, dst)\n            ok = True\n        except OSError:\n            try:\n                r = subprocess.run(\n                    ["/bin/cp", "-p", src, dst],\n                    stdout=subprocess.DEVNULL,\n                    stderr=subprocess.DEVNULL,\n                    timeout=30,\n                    check=False,\n                )\n                ok = r.returncode == 0 and os.path.lexists(dst)\n            except Exception:\n                ok = False\n        if ok:\n            n += 1\n        else:\n            skipped += 1\n\nif n == 0:\n    print("staged=0 skipped=%d archive_rc=0" % skipped, flush=True)\n    open(archive, "wb").close()\n    sys.exit(0)\n\nrc = 1\ntry:\n    rc = subprocess.run(\n        ["/usr/bin/ditto", "-c", stage, archive],\n        timeout=max(120, min(3600, n // 10 + 60)),\n        check=False,\n    ).returncode\nexcept Exception:\n    rc = 1\n\narch_ok = os.path.isfile(archive) and os.path.getsize(archive) > 0\nif rc != 0 and arch_ok:\n    rc = 0\nprint("staged=%d skipped=%d archive_rc=%d" % (n, skipped, rc), flush=True)\n# Soft-ok empty transfer only when nothing staged; never claim success with missing archive\nif n > 0 and not arch_ok:\n    sys.exit(1)\nsys.exit(0 if arch_ok or n == 0 else rc)\n'


@dataclass(frozen=True)
class FileMeta:
    mtime_ns: int
    size: int


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


# Prefer these roots first so push starts useful data before iCloud trees.
_INV_PREF = ("Developer", "Downloads", ".ssh", ".config", "Desktop", "Documents")


def _inv_skip_names() -> frozenset[str]:
    """Dir basenames that hang or bloat inventory (cloud FUSE, VCS, caches).

    ``Library`` is only skipped on full-home walks; explicit includes under
    Library/ (e.g. library-app preset) still walk.
    """
    try:
        from maccluster.constants import SYNC_INV_SKIP_DIR_NAMES

        return SYNC_INV_SKIP_DIR_NAMES
    except Exception:
        return frozenset(
            {
                "imessage_export",
                "node_modules",
                ".git",
                "DerivedData",
                "__pycache__",
                ".venv",
                "venv",
                ".Trash",
                "Library",
            }
        )


_INV_SKIP_NAMES = _inv_skip_names()
_UF_DATALESS = 0x40000000


def _safe_scandir(
    path: Path | str,
    *,
    timeout_s: float = 6.0,
) -> list[tuple[str, str, bool, bool]] | None:
    """List directory in a killable child — iCloud/FP hangs ignore SIGALRM."""
    import json
    import subprocess
    import sys

    code = (
        "import os,json,sys\n"
        "p=sys.argv[1]\n"
        "o=[]\n"
        "try:\n"
        "  for e in os.scandir(p):\n"
        "    try:\n"
        "      o.append([e.name,e.path,e.is_dir(follow_symlinks=False),"
        "e.is_file(follow_symlinks=False)])\n"
        "    except OSError:\n"
        "      pass\n"
        "except Exception:\n"
        "  sys.exit(2)\n"
        "print(json.dumps(o))\n"
    )
    try:
        r = subprocess.run(
            [sys.executable, "-c", code, str(path)],
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except Exception:
        return None
    if r.returncode != 0:
        return None
    try:
        raw = json.loads(r.stdout or "[]")
        out: list[tuple[str, str, bool, bool]] = []
        for row in raw:
            if len(row) >= 4:
                out.append((str(row[0]), str(row[1]), bool(row[2]), bool(row[3])))
        return out
    except Exception:
        return None


def inventory_local(
    root: Path,
    excludes: tuple[str, ...],
    includes: tuple[str, ...] = (),
    *,
    progress: ProgressLike | None = None,
    max_sec: float | None = None,
    dir_sec: float | None = None,
) -> dict[str, FileMeta]:
    """Walk home (or only ``includes`` roots); regular files + symlinks only.

    Hang-safe for iCloud Desktop/Documents (killable scandir child). Fast
    ``os.walk`` for Developer/Downloads/.ssh/.config. Skips ``UF_DATALESS``.
    When *includes* is set, only those subtrees are walked. Optional *progress*
    reports live file counts so the bar is not stuck at 0%.
    """
    prog = progress or NullProgress()
    out: dict[str, FileMeta] = {}
    root = root.expanduser()
    try:
        root = root.resolve()
    except OSError:
        root = root.absolute()
    max_s = float(
        max_sec if max_sec is not None else os.environ.get("MACCLUSTER_INV_MAX_SEC", "240")
    )
    dir_s = float(dir_sec if dir_sec is not None else os.environ.get("MACCLUSTER_INV_DIR_SEC", "6"))
    t0 = time.time()
    n_emit = 0
    bytes_emit = 0
    last_prog = 0.0

    def _budget_ok() -> bool:
        return (time.time() - t0) <= max_s

    def _tick(detail: str) -> None:
        nonlocal last_prog
        now = time.time()
        if now - last_prog < 0.2 and n_emit % 1000 != 0:
            return
        last_prog = now
        elapsed = int(now - t0)
        prog.update(
            phase="inventory",
            direction="local",
            files_done=n_emit,
            bytes_done=bytes_emit,
            detail=f"{n_emit} files · {elapsed}s · {detail}",
            path=detail,
            force=True,
        )

    def _emit_path(path: Path, rel: str) -> bool:
        nonlocal n_emit, bytes_emit
        if is_excluded(rel, excludes):
            return False
        try:
            st = path.lstat()
        except OSError:
            return False
        if not (stat.S_ISREG(st.st_mode) or stat.S_ISLNK(st.st_mode)):
            return False
        if getattr(st, "st_flags", 0) & _UF_DATALESS:
            return False
        out[rel] = FileMeta(mtime_ns=st.st_mtime_ns, size=st.st_size)
        n_emit += 1
        bytes_emit += max(0, int(st.st_size))
        if n_emit % 250 == 0:
            _tick(rel)
        return True

    def _fast_walk(walk_path: Path, label: str) -> None:
        """os.walk for trees that do not hang (Developer, Downloads, …)."""
        for dirpath, dirnames, filenames in os.walk(walk_path, followlinks=False):
            if not _budget_ok():
                return
            rel_dir = os.path.relpath(dirpath, root)
            if rel_dir == ".":
                rel_dir = ""
            keep: list[str] = []
            for d in dirnames:
                if d in _INV_SKIP_NAMES:
                    continue
                if d.startswith(".") and d not in (".ssh", ".config"):
                    continue
                rel = f"{rel_dir}/{d}" if rel_dir else d
                rel = rel.replace("\\", "/")
                if is_excluded(rel, excludes) or is_excluded(rel + "/", excludes):
                    continue
                keep.append(d)
            dirnames[:] = keep
            for name in filenames:
                if name == ".DS_Store":
                    continue
                rel = f"{rel_dir}/{name}" if rel_dir else name
                rel = rel.replace("\\", "/")
                _emit_path(Path(dirpath) / name, rel)
            _tick(label)

    def _safe_walk(start: str, label: str) -> None:
        """Killable scandir walk for iCloud Desktop/Documents."""
        stack = [start]
        while stack and _budget_ok():
            cur = stack.pop()
            entries = _safe_scandir(cur, timeout_s=dir_s)
            if entries is None:
                try:
                    rel_h = os.path.relpath(cur, root).replace("\\", "/")
                except Exception:
                    rel_h = cur
                prog.note(f"  skip-hang local: {rel_h}")
                continue
            for name, path, is_dir, _is_file in entries:
                if not _budget_ok():
                    break
                if name in _INV_SKIP_NAMES or name == ".DS_Store":
                    continue
                if name.startswith(".") and name not in (".ssh", ".config"):
                    if is_dir:
                        continue
                if is_dir:
                    rel = os.path.relpath(path, root).replace("\\", "/")
                    if is_excluded(rel, excludes) or is_excluded(rel + "/", excludes):
                        continue
                    stack.append(path)
                else:
                    rel = os.path.relpath(path, root).replace("\\", "/")
                    _emit_path(Path(path), rel)
            _tick(label)

    # Prefer Developer/Downloads before iCloud Desktop/Documents
    raw_includes = [i.replace("\\", "/").strip("/").rstrip("/") for i in includes if i]
    raw_includes = [i for i in raw_includes if i and ".." not in i.split("/")]
    raw_includes.sort(
        key=lambda x: _INV_PREF.index(x.split("/")[0]) if x.split("/")[0] in _INV_PREF else 99
    )

    # (path, label, safe_mode)
    walk_jobs: list[tuple[str, str, bool]] = []
    if raw_includes:
        for inc in raw_includes:
            p0 = os.path.join(str(root), inc)
            if not os.path.lexists(p0):
                continue
            base = inc.split("/")[0]
            hang_prone = base in ("Documents", "Desktop")
            if hang_prone and "/" not in inc:
                kids = _safe_scandir(p0, timeout_s=dir_s)
                if kids is None:
                    prog.note(f"  skip-hang local: {inc}")
                    continue
                for name, path, is_dir, is_file in kids:
                    if name in _INV_SKIP_NAMES or name == ".DS_Store":
                        continue
                    if is_dir:
                        walk_jobs.append((path, f"{inc}/{name}", True))
                    elif is_file:
                        rel = os.path.relpath(path, root).replace("\\", "/")
                        _emit_path(Path(path), rel)
            else:
                walk_jobs.append((p0, inc, hang_prone))
    else:
        # Full home: safe mode (Library skipped by name)
        walk_jobs.append((str(root), ".", True))

    for walk_path, label, safe_mode in walk_jobs:
        if not _budget_ok():
            prog.note("  local inventory time budget reached (partial)")
            break
        prog.note(f"  local walk: {label}")
        _tick(label)
        if safe_mode:
            _safe_walk(walk_path, label)
        else:
            _fast_walk(Path(walk_path), label)

    if not _budget_ok():
        prog.note(f"  local inventory partial: {len(out)} files (budget {int(max_s)}s)")
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
    *,
    policy: str = "newer",
    remote_complete: bool = True,
) -> tuple[list[str], list[str], dict[str, int]]:
    """
    Return (to_push, to_pull, stats).

    ``remote_complete`` says whether the remote inventory covers the whole
    tree. When the remote walk stopped early (time budget, hung directory),
    a file missing from *remote* means "not looked at", not "not there" —
    pushing it would re-copy data the peer already has. Those land in
    ``stats["remote_unknown"]`` and are left for the next run instead.

    Policies (CCC-inspired):
      newer          — mtime newest-wins (default)
      larger         — larger size wins (mtime tie-break)
      prefer-local   — on conflict always push local
      prefer-remote  — on conflict always pull remote
      skip-conflict  — only missing files; never overwrite
    """
    to_push: list[str] = []
    to_pull: list[str] = []
    stats = {
        "only_local": 0,
        "only_remote": 0,
        "remote_unknown": 0,
        "local_newer": 0,
        "remote_newer": 0,
        "equal": 0,
        "conflicts_skipped": 0,
    }
    all_rels = set(local) | set(remote)
    for rel in all_rels:
        lm = local.get(rel)
        rm = remote.get(rel)
        if lm is not None and rm is None:
            if not remote_complete:
                # Unlisted under a truncated walk: unknown, not absent.
                stats["remote_unknown"] += 1
                continue
            to_push.append(rel)
            stats["only_local"] += 1
            continue
        if rm is not None and lm is None:
            to_pull.append(rel)
            stats["only_remote"] += 1
            continue
        if lm is None or rm is None:
            continue
        # both exist
        same = lm.mtime_ns == rm.mtime_ns and lm.size == rm.size
        if same or (lm.mtime_ns == rm.mtime_ns and policy == "newer"):
            if lm.mtime_ns == rm.mtime_ns and lm.size == rm.size:
                stats["equal"] += 1
                continue
            if lm.mtime_ns == rm.mtime_ns and policy == "newer":
                stats["equal"] += 1
                continue

        if policy == "skip-conflict":
            stats["conflicts_skipped"] += 1
            continue

        if policy == "prefer-local":
            if lm.mtime_ns != rm.mtime_ns or lm.size != rm.size:
                to_push.append(rel)
                if lm.mtime_ns >= rm.mtime_ns:
                    stats["local_newer"] += 1
                else:
                    stats["local_newer"] += 1  # forced
            continue

        if policy == "prefer-remote":
            if lm.mtime_ns != rm.mtime_ns or lm.size != rm.size:
                to_pull.append(rel)
                stats["remote_newer"] += 1
            continue

        if policy == "larger":
            if lm.size > rm.size:
                to_push.append(rel)
                stats["local_newer"] += 1
            elif rm.size > lm.size:
                to_pull.append(rel)
                stats["remote_newer"] += 1
            elif lm.mtime_ns > rm.mtime_ns:
                to_push.append(rel)
                stats["local_newer"] += 1
            elif rm.mtime_ns > lm.mtime_ns:
                to_pull.append(rel)
                stats["remote_newer"] += 1
            else:
                stats["equal"] += 1
            continue

        # newer (default)
        if lm.mtime_ns > rm.mtime_ns:
            to_push.append(rel)
            stats["local_newer"] += 1
        elif rm.mtime_ns > lm.mtime_ns:
            to_pull.append(rel)
            stats["remote_newer"] += 1
        else:
            stats["equal"] += 1

    to_push.sort()
    to_pull.sort()
    return to_push, to_pull, stats


def apply_batch_limits(
    to_push: list[str],
    to_pull: list[str],
    push_sizes: dict[str, int],
    pull_sizes: dict[str, int],
    *,
    max_files: int | None,
    max_bytes: int | None,
) -> tuple[list[str], list[str], bool]:
    """Cap transfer lists; prefer smaller files first so many finish per run."""
    if max_files is None and max_bytes is None:
        return to_push, to_pull, False

    # Merge candidates ordered by size, tag direction
    cands: list[tuple[int, str, str]] = []
    for r in to_push:
        cands.append((push_sizes.get(r, 0), "push", r))
    for r in to_pull:
        cands.append((pull_sizes.get(r, 0), "pull", r))
    cands.sort(key=lambda t: (t[0], t[1], t[2]))

    out_push: list[str] = []
    out_pull: list[str] = []
    files = 0
    bytes_ = 0
    for sz, direction, rel in cands:
        if max_files is not None and files >= max_files:
            return sorted(out_push), sorted(out_pull), True
        if max_bytes is not None and files > 0 and bytes_ + sz > max_bytes:
            return sorted(out_push), sorted(out_pull), True
        if direction == "push":
            out_push.append(rel)
        else:
            out_pull.append(rel)
        files += 1
        bytes_ += sz
    truncated = len(out_push) + len(out_pull) < len(to_push) + len(to_pull)
    return sorted(out_push), sorted(out_pull), truncated


def classify_compare(
    local: dict[str, FileMeta],
    remote: dict[str, FileMeta],
) -> dict[str, list[str]]:
    """Buckets for --compare (no transfer)."""
    only_local: list[str] = []
    only_remote: list[str] = []
    local_newer: list[str] = []
    remote_newer: list[str] = []
    equal: list[str] = []
    for rel in sorted(set(local) | set(remote)):
        lm, rm = local.get(rel), remote.get(rel)
        if lm and not rm:
            only_local.append(rel)
        elif rm and not lm:
            only_remote.append(rel)
        elif lm and rm:
            if lm.mtime_ns > rm.mtime_ns:
                local_newer.append(rel)
            elif rm.mtime_ns > lm.mtime_ns:
                remote_newer.append(rel)
            else:
                equal.append(rel)
    return {
        "only_local": only_local,
        "only_remote": only_remote,
        "local_newer": local_newer,
        "remote_newer": remote_newer,
        "equal": equal,
    }


@dataclass(frozen=True)
class DeltaBucket:
    """One inventory-diff bucket with count + total bytes + sample paths."""

    count: int
    bytes: int
    samples: tuple[str, ...] = ()


@dataclass(frozen=True)
class PreciseDelta:
    """Inventory → compare result: exact file deltas, not bulk size guesses.

    Built from local/remote inventories (relpath → mtime_ns + size) and the
    same conflict policy as ``plan_transfers``. Transfer lists are the only
    payload that should be synced (difference only).
    """

    policy: str
    local_files: int
    remote_files: int
    local_bytes: int
    remote_bytes: int
    only_local: DeltaBucket
    only_remote: DeltaBucket
    local_newer: DeltaBucket
    remote_newer: DeltaBucket
    equal: DeltaBucket
    conflicts_skipped: int
    to_push: tuple[str, ...]
    to_pull: tuple[str, ...]
    push_bytes: int
    pull_bytes: int

    @property
    def delta_files(self) -> int:
        return len(self.to_push) + len(self.to_pull)

    @property
    def delta_bytes(self) -> int:
        return self.push_bytes + self.pull_bytes

    @property
    def in_sync(self) -> bool:
        return self.delta_files == 0


def _bucket_from(
    rels: list[str],
    inv: dict[str, FileMeta],
    *,
    sample: int = 8,
) -> DeltaBucket:
    total = 0
    for r in rels:
        m = inv.get(r)
        if m is not None:
            total += max(0, int(m.size))
    samples = tuple(f"{r} ({format_bytes(inv[r].size)})" if r in inv else r for r in rels[:sample])
    return DeltaBucket(count=len(rels), bytes=total, samples=samples)


def precise_delta(
    local: dict[str, FileMeta],
    remote: dict[str, FileMeta],
    *,
    policy: str = "newer",
    sample: int = 8,
) -> PreciseDelta:
    """Read two inventories, classify exact deltas, plan difference transfer.

    Pure function — no I/O. Prefer this over bulk ``du``/full-tree copies:
    only missing/newer files (by policy) enter ``to_push`` / ``to_pull``.
    """
    buckets = classify_compare(local, remote)
    to_push, to_pull, stats = plan_transfers(local, remote, policy=policy)
    push_bytes = sum(max(0, int(local[r].size)) for r in to_push if r in local)
    pull_bytes = sum(max(0, int(remote[r].size)) for r in to_pull if r in remote)
    return PreciseDelta(
        policy=policy,
        local_files=len(local),
        remote_files=len(remote),
        local_bytes=sum(max(0, int(m.size)) for m in local.values()),
        remote_bytes=sum(max(0, int(m.size)) for m in remote.values()),
        only_local=_bucket_from(buckets["only_local"], local, sample=sample),
        only_remote=_bucket_from(buckets["only_remote"], remote, sample=sample),
        local_newer=_bucket_from(buckets["local_newer"], local, sample=sample),
        remote_newer=_bucket_from(buckets["remote_newer"], remote, sample=sample),
        equal=_bucket_from(buckets["equal"], local, sample=sample),
        conflicts_skipped=int(stats.get("conflicts_skipped", 0)),
        to_push=tuple(to_push),
        to_pull=tuple(to_pull),
        push_bytes=push_bytes,
        pull_bytes=pull_bytes,
    )


def format_precise_delta(
    delta: PreciseDelta,
    *,
    peer_id: str,
    peer_ip: str = "",
) -> list[str]:
    """Human-readable lines for one peer delta report."""
    where = f"{peer_id}" + (f" ({peer_ip})" if peer_ip else "")
    lines = [
        f"delta vs {where}  policy={delta.policy}",
        f"  inventory: local={delta.local_files:,} files "
        f"({format_bytes(delta.local_bytes)}) · "
        f"remote={delta.remote_files:,} files ({format_bytes(delta.remote_bytes)})",
        f"  buckets: only_local={delta.only_local.count:,}/"
        f"{format_bytes(delta.only_local.bytes)}  "
        f"only_remote={delta.only_remote.count:,}/"
        f"{format_bytes(delta.only_remote.bytes)}  "
        f"local_newer={delta.local_newer.count:,}/"
        f"{format_bytes(delta.local_newer.bytes)}  "
        f"remote_newer={delta.remote_newer.count:,}/"
        f"{format_bytes(delta.remote_newer.bytes)}  "
        f"equal={delta.equal.count:,}",
        f"  plan: push {len(delta.to_push):,} files "
        f"({format_bytes(delta.push_bytes)}) · "
        f"pull {len(delta.to_pull):,} files ({format_bytes(delta.pull_bytes)}) · "
        f"delta_total={format_bytes(delta.delta_bytes)}",
    ]
    if delta.conflicts_skipped:
        lines.append(f"  conflicts_skipped={delta.conflicts_skipped:,}")
    if delta.in_sync:
        lines.append("  status: in sync (no delta)")
    else:
        lines.append(f"  status: {delta.delta_files:,} files differ")
    if delta.only_local.samples or delta.local_newer.samples:
        for s in (delta.only_local.samples + delta.local_newer.samples)[:6]:
            lines.append(f"    push + {s}")
        extra = len(delta.to_push) - min(6, len(delta.to_push))
        if extra > 0:
            lines.append(f"    push … +{extra} more")
    if delta.only_remote.samples or delta.remote_newer.samples:
        for s in (delta.only_remote.samples + delta.remote_newer.samples)[:6]:
            lines.append(f"    pull + {s}")
        extra = len(delta.to_pull) - min(6, len(delta.to_pull))
        if extra > 0:
            lines.append(f"    pull … +{extra} more")
    return lines


# Large ditto CPIO archives (>~2–4 GiB) often fail with "cpio read error" after scp.
# Auto-split into smaller batches.
SYNC_CHUNK_BYTES = 2 * 1024 * 1024 * 1024  # 2 GiB payload per archive
SYNC_CHUNK_FILES = 120
# Single files above this skip CPIO (ditto -c/-x often fails ~10+ GiB with "cpio read error")
SYNC_LARGE_FILE_BYTES = 3 * 1024 * 1024 * 1024  # 3 GiB → direct scp


def _chunk_rels(
    rels: list[str],
    sizes: dict[str, int],
    *,
    max_bytes: int = SYNC_CHUNK_BYTES,
    max_files: int = SYNC_CHUNK_FILES,
) -> list[list[str]]:
    """Split transfer list into size/count-limited batches (preserves order)."""
    if not rels:
        return []
    batches: list[list[str]] = []
    cur: list[str] = []
    cur_b = 0
    for r in rels:
        sz = int(sizes.get(r, 0) or 0)
        if cur and (len(cur) >= max_files or (max_bytes > 0 and cur_b + sz > max_bytes)):
            batches.append(cur)
            cur = []
            cur_b = 0
        cur.append(r)
        cur_b += sz
    if cur:
        batches.append(cur)
    return batches


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


def _split_large_files(rels: list[str], sizes: dict[str, int]) -> tuple[list[str], list[str]]:
    """Return (normal_rels, large_rels) where large is direct-scp territory."""
    normal: list[str] = []
    large: list[str] = []
    for r in rels:
        if int(sizes.get(r, 0) or 0) >= SYNC_LARGE_FILE_BYTES:
            large.append(r)
        else:
            normal.append(r)
    return normal, large


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
    includes: tuple[str, ...] = (),
    include_dotdirs: bool = False,
    safe_scandir: bool = False,
) -> tuple[dict[str, FileMeta] | None, str, bool]:
    """Returns (inventory, note, complete).

    ``complete`` is False when the walk stopped early — time budget or a hung
    directory. Callers must not read "missing from the inventory" as "missing
    on the peer" in that case.
    """
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
            return None, (scp.stderr or f"scp {local.name} failed")[:300], False

    # PYTHONUNBUFFERED so inventory lines are not stuck in libc buffers if SSH dies
    env_pairs = ["env", "PYTHONUNBUFFERED=1"]
    if include_dotdirs:
        env_pairs.append("MACCLUSTER_INV_DOTDIRS=1")
    if safe_scandir:
        # Home can reach iCloud/FileProvider paths where scandir wedges; pay the
        # per-directory child process there. ~/Developer never does, so `dev`
        # keeps the fast in-process walk.
        env_pairs.append("MACCLUSTER_INV_SAFE_SCANDIR=1")
    r = ctx.runner.run(
        _ssh_argv(
            abs_ssh,
            ssh_target,
            *env_pairs,
            "/usr/bin/python3",
            "-u",
            remote_script,
            remote_home,
            remote_excl,
            *[inc.rstrip("/") for inc in includes if inc.strip()],
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
    inv = parse_inventory_text(r.stdout or "")
    err = (r.stderr or "").strip()
    # Accept partial inventory on timeout/kill if we listed any files
    if inv:
        if r.returncode != 0:
            return inv, f"partial inventory ({len(inv)} files); {err[:120]}", False
        if "# inventory time budget" in err or "# skip-hang" in err:
            return inv, f"partial inventory ({len(inv)} files); {err[:120]}", False
        return inv, "", True
    # Empty stdout: if remote walk started (stderr markers), soft-empty so push can proceed
    hangish = any(
        m in err
        for m in (
            "# walk",
            "# skip-hang",
            "# inventory",
            "# listed",
            "inventory time budget",
        )
    )
    if r.returncode != 0 or hangish:
        if hangish or r.returncode in (124, -9, -15, 255):
            return {}, f"empty/partial inventory (peer hang or timeout); {err[:160]}", False
        return None, (err or r.stdout or "remote inventory failed")[:300], False
    return inv, "", True


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
    via_n = (via or "tb").strip().lower()
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
            done_bytes = 0
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
                        stream=not no_stream,
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
