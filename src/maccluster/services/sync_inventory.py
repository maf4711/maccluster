"""Inventory side of the home sync: local walk, remote walk, exclude matching.

Extracted verbatim from ``sync_service``. ``FileMeta`` is the per-file record
(mtime_ns + size) that both inventories produce and the planner consumes.
"""

from __future__ import annotations

import os
import stat
import time
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path

from maccluster.app_factory import AppContext
from maccluster.render.progress import NullProgress, ProgressLike
from maccluster.services.sync_ssh import _scp_argv, _ssh_argv

# Remote inventory: argv home excludes_file → lines relpath\\tmtime_ns\\tsize
_REMOTE_INVENTORY_PY = 'import fnmatch, json, os, signal, stat, subprocess, sys, time\n\n# Unbuffered inventory lines (SSH non-TTY otherwise loses stdout on kill/timeout)\ntry:\n    sys.stdout.reconfigure(line_buffering=True)\nexcept Exception:\n    pass\ntry:\n    sys.stderr.reconfigure(line_buffering=True)\nexcept Exception:\n    pass\n\nroot, ex_path = sys.argv[1], sys.argv[2]\nincludes = [x.strip().strip("/") for x in sys.argv[3:] if x.strip()]\nex = open(ex_path, encoding="utf-8").read().splitlines() if os.path.isfile(ex_path) else []\nPREF = ("Developer", "Downloads", ".ssh", ".config", "Desktop", "Documents")\nincludes.sort(key=lambda x: PREF.index(x.split("/")[0]) if x.split("/")[0] in PREF else 99)\nt0 = time.time()\nMAX_SEC = float(os.environ.get("MACCLUSTER_INV_MAX_SEC", "900"))\nDIR_SEC = float(os.environ.get("MACCLUSTER_INV_DIR_SEC", "6"))\nSKIP_NAMES = {\n    "imessage_export", "node_modules", ".git", "DerivedData",\n    "__pycache__", ".venv", "venv", ".Trash", "Library",\n}\nDOTDIRS = os.environ.get("MACCLUSTER_INV_DOTDIRS", "").strip().lower() in ("1", "true", "yes")\n# Per-directory child processes cost one interpreter start per folder: measured\n# 167 files/s, so a 4.4M-file tree needs 7h and always trips MAX_SEC. Only cloud\n# providers (iCloud/FileProvider) can wedge scandir, so the guard is opt-in.\nSAFE_SCANDIR = os.environ.get("MACCLUSTER_INV_SAFE_SCANDIR", "").strip().lower() in ("1", "true", "yes")\nif DOTDIRS:\n    SKIP_NAMES.discard(".git")\nUF_DATALESS = 0x40000000\nn_emitted = 0\n\n\ndef excl(rel):\n    rel = rel.replace("\\\\", "/").lstrip("./")\n    parts = rel.split("/")\n    for pat in ex:\n        if not pat:\n            continue\n        p = pat.replace("\\\\", "/")\n        if p.endswith("/"):\n            b = p.rstrip("/")\n            if rel == b or rel.startswith(b + "/"):\n                return True\n            if b.startswith("**/") and (b[3:] in parts or any(fnmatch.fnmatch(x, b[3:]) for x in parts)):\n                return True\n        elif p.startswith("**/"):\n            rest = p[3:]\n            if any(x == rest or fnmatch.fnmatch(x, rest) for x in parts):\n                return True\n            if fnmatch.fnmatch(rel, p) or fnmatch.fnmatch(os.path.basename(rel), rest):\n                return True\n        else:\n            if fnmatch.fnmatch(rel, p) or fnmatch.fnmatch(os.path.basename(rel), p):\n                return True\n            b = p.rstrip("/")\n            if rel == b or rel.startswith(b + "/"):\n                return True\n    return False\n\n\ndef safe_scandir(path):\n    """List dir. Fast in-process scandir; killable child only when asked."""\n    if not SAFE_SCANDIR:\n        try:\n            out = []\n            for e in os.scandir(path):\n                try:\n                    out.append([e.name, e.path, e.is_dir(follow_symlinks=False), e.is_file(follow_symlinks=False)])\n                except OSError:\n                    pass\n            return out\n        except OSError:\n            return None\n    code = (\n        "import os,json,sys\\n"\n        "p=sys.argv[1]\\n"\n        "o=[]\\n"\n        "try:\\n"\n        "  for e in os.scandir(p):\\n"\n        "    try:\\n"\n        "      o.append([e.name,e.path,e.is_dir(follow_symlinks=False),e.is_file(follow_symlinks=False)])\\n"\n        "    except OSError:\\n"\n        "      pass\\n"\n        "except Exception:\\n"\n        "  sys.exit(2)\\n"\n        "print(json.dumps(o))\\n"\n    )\n    try:\n        r = subprocess.run(\n            [sys.executable, "-c", code, path],\n            capture_output=True,\n            text=True,\n            timeout=DIR_SEC,\n        )\n    except subprocess.TimeoutExpired:\n        print("# skip-hang %s" % path, file=sys.stderr, flush=True)\n        return None\n    if r.returncode != 0:\n        return None\n    try:\n        return json.loads(r.stdout or "[]")\n    except Exception:\n        return None\n\n\ndef emit_file(home, path):\n    global n_emitted\n    try:\n        st = os.lstat(path)\n    except OSError:\n        return False\n    if not (stat.S_ISREG(st.st_mode) or stat.S_ISLNK(st.st_mode)):\n        return False\n    if getattr(st, "st_flags", 0) & UF_DATALESS:\n        return False\n    rel = os.path.relpath(path, home).replace("\\\\", "/")\n    if excl(rel):\n        return False\n    sys.stdout.write("%s\\t%d\\t%d\\n" % (rel, st.st_mtime_ns, st.st_size))\n    n_emitted += 1\n    if n_emitted % 200 == 0:\n        sys.stdout.flush()\n    return True\n\n\ndef walk_safe(home, start):\n    n = 0\n    stack = [start]\n    while stack:\n        if time.time() - t0 > MAX_SEC:\n            print("# inventory time budget", file=sys.stderr, flush=True)\n            break\n        cur = stack.pop()\n        entries = safe_scandir(cur)\n        if entries is None:\n            try:\n                label = os.path.relpath(cur, home)\n            except Exception:\n                label = cur\n            print("# skip-hang %s" % label, file=sys.stderr, flush=True)\n            continue\n        for name, path, is_dir, is_file in entries:\n            if time.time() - t0 > MAX_SEC:\n                break\n            if name in SKIP_NAMES:\n                continue\n            if name == ".DS_Store":\n                continue\n            # skip heavy/hidden dirs except .ssh / .config (DOTDIRS walks .git/.github)\n            if (not DOTDIRS) and name.startswith(".") and name not in (".ssh", ".config"):\n                if is_dir:\n                    continue\n            if is_dir:\n                rel = os.path.relpath(path, home).replace("\\\\", "/")\n                if excl(rel) or excl(rel + "/"):\n                    continue\n                stack.append(path)\n            elif is_file or True:\n                if emit_file(home, path):\n                    n += 1\n                    if n % 20000 == 0:\n                        print("# listed %d" % n, file=sys.stderr, flush=True)\n    return n\n\n\nwalk_roots = []\nif includes:\n    for inc in includes:\n        if not inc or ".." in inc.split("/"):\n            continue\n        p0 = os.path.join(root, inc)\n        if not os.path.lexists(p0):\n            continue\n        base = inc.split("/")[0]\n        if base in ("Documents", "Desktop") and "/" not in inc.rstrip("/"):\n            kids = safe_scandir(p0)\n            if kids is None:\n                print("# skip-hang %s" % inc, file=sys.stderr, flush=True)\n                continue\n            for name, path, is_dir, is_file in kids:\n                if name in SKIP_NAMES or name == ".DS_Store":\n                    continue\n                if is_dir:\n                    walk_roots.append((path, "%s/%s" % (inc.rstrip("/"), name)))\n                elif is_file:\n                    emit_file(root, path)\n        else:\n            walk_roots.append((p0, inc))\nelse:\n    walk_roots.append((root, ""))\n\nn = 0\nfor walk_root, label in walk_roots:\n    if time.time() - t0 > MAX_SEC:\n        break\n    print("# walk %s" % label, file=sys.stderr, flush=True)\n    n += walk_safe(root, walk_root)\n\nsys.stdout.flush()\nprint("# inventory done n=%d sec=%d" % (n_emitted, int(time.time() - t0)), file=sys.stderr, flush=True)\nsys.exit(0)\n'


@dataclass(frozen=True)
class FileMeta:
    mtime_ns: int
    size: int


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
