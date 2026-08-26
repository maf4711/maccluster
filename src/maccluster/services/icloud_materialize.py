"""Force iCloud Drive materialization for UF_DATALESS (placeholder) files.

macOS Desktop & Documents via iCloud File Provider store many files as
*dataless* stubs (``UF_DATALESS``). Opening them for ditto/rsync hangs until
iCloud downloads content — or forever offline.

This module:
  1. Queues ``brctl download`` on roots and individual stubs
  2. Force-reads each dataless file with a per-file timeout (subprocess)
  3. Reports remaining dataless counts after the pass

Used by ``maccluster sync home --force-icloud`` / ``--identical``.
"""

from __future__ import annotations

import os
import stat
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

# Darwin st_flags: file is a dataless (iCloud) placeholder
UF_DATALESS = 0x40000000


def is_dataless_stat(st: os.stat_result) -> bool:
    """True if lstat result is an iCloud dataless placeholder."""
    flags = getattr(st, "st_flags", 0) or 0
    return bool(flags & UF_DATALESS)


def is_dataless_path(path: Path | str) -> bool:
    try:
        return is_dataless_stat(os.lstat(path))
    except OSError:
        return False


@dataclass
class MaterializeResult:
    root: str
    scanned: int = 0
    dataless_found: int = 0
    materialized: int = 0
    failed: int = 0
    remaining_dataless: int = 0
    seconds: float = 0.0
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.failed == 0 or self.materialized > 0


def _should_skip_dir(name: str) -> bool:
    return name in {
        ".git",
        "node_modules",
        ".Trash",
        "Library",
        "__pycache__",
        ".venv",
        "venv",
        "DerivedData",
        ".next",
    }


def _brctl_download(path: str | Path, *, timeout: float = 8.0) -> None:
    brctl = "/usr/bin/brctl"
    if not os.path.isfile(brctl):
        return
    try:
        subprocess.run(
            [brctl, "download", str(path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError):
        pass


def _force_read(path: str, *, timeout: float) -> bool:
    """Read first bytes in a child process so we can kill hangs."""
    code = "import sys\np = sys.argv[1]\nwith open(p, 'rb') as f:\n    f.read(16384)\n"
    try:
        r = subprocess.run(
            [sys.executable, "-c", code, path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
            check=False,
        )
        return r.returncode == 0
    except subprocess.TimeoutExpired:
        return False
    except OSError:
        return False


def materialize_tree(
    root: Path | str,
    *,
    timeout_per_file: float = 20.0,
    max_seconds: float = 600.0,
    max_files: int | None = None,
    note: Callable[[str], None] | None = None,
) -> MaterializeResult:
    """Walk *root*, force-download iCloud dataless files.

    Parameters
    ----------
    timeout_per_file:
        Seconds to wait for one file to materialize (open+read).
    max_seconds:
        Overall budget for this tree.
    max_files:
        Optional cap on how many dataless files to attempt.
    note:
        Optional callback ``note(str)`` for progress lines.
    """
    root_p = Path(root).expanduser()
    result = MaterializeResult(root=str(root_p))
    if not root_p.is_dir():
        result.notes.append(f"missing: {root_p}")
        return result

    t0 = time.monotonic()
    log = note or (lambda _m: None)

    # Queue whole tree with brctl first (best-effort, non-blocking-ish)
    _brctl_download(root_p, timeout=15.0)
    log(f"icloud: brctl download queued for {root_p}")

    attempted = 0
    for dirpath, dirnames, filenames in os.walk(root_p, followlinks=False):
        if time.monotonic() - t0 > max_seconds:
            result.notes.append("time budget exhausted")
            break
        dirnames[:] = [d for d in dirnames if not _should_skip_dir(d)]
        for name in filenames:
            if time.monotonic() - t0 > max_seconds:
                result.notes.append("time budget exhausted")
                break
            if max_files is not None and attempted >= max_files:
                result.notes.append(f"max_files={max_files} reached")
                break
            path = os.path.join(dirpath, name)
            result.scanned += 1
            try:
                st = os.lstat(path)
            except OSError:
                continue
            if not stat.S_ISREG(st.st_mode):
                continue
            if not is_dataless_stat(st):
                continue
            result.dataless_found += 1
            attempted += 1
            _brctl_download(path, timeout=5.0)
            if _force_read(path, timeout=timeout_per_file):
                # verify flag cleared
                try:
                    st2 = os.lstat(path)
                    if not is_dataless_stat(st2):
                        result.materialized += 1
                    else:
                        # content readable but flag may lag — count as success
                        result.materialized += 1
                except OSError:
                    result.materialized += 1
                if result.materialized % 25 == 0:
                    log(
                        f"icloud: materialized {result.materialized}/"
                        f"{result.dataless_found} under {root_p.name}"
                    )
            else:
                result.failed += 1

    # remaining count (fast lstat pass)
    rem = 0
    for dirpath, dirnames, filenames in os.walk(root_p, followlinks=False):
        dirnames[:] = [d for d in dirnames if not _should_skip_dir(d)]
        for name in filenames:
            p = os.path.join(dirpath, name)
            try:
                st = os.lstat(p)
            except OSError:
                continue
            if stat.S_ISREG(st.st_mode) and is_dataless_stat(st):
                rem += 1
    result.remaining_dataless = rem
    result.seconds = time.monotonic() - t0
    log(
        f"icloud: {root_p.name} done mat={result.materialized} "
        f"fail={result.failed} remaining_dataless={rem} "
        f"t={result.seconds:.0f}s"
    )
    return result


def materialize_homes(
    roots: list[str | Path],
    *,
    timeout_per_file: float = 20.0,
    max_seconds_per_root: float = 600.0,
    note: Callable[[str], None] | None = None,
) -> list[MaterializeResult]:
    """Materialize several roots (Desktop, Documents, …)."""
    out: list[MaterializeResult] = []
    for root in roots:
        out.append(
            materialize_tree(
                root,
                timeout_per_file=timeout_per_file,
                max_seconds=max_seconds_per_root,
                note=note,
            )
        )
    return out


# Remote script: run materialize on peer (Desktop + Documents by default)
REMOTE_MATERIALIZE_PY = r"""
import os, stat, subprocess, sys, time
UF_DATALESS = 0x40000000
timeout_per = float(sys.argv[1]) if len(sys.argv) > 1 else 20.0
max_sec = float(sys.argv[2]) if len(sys.argv) > 2 else 600.0
roots = sys.argv[3:] if len(sys.argv) > 3 else ["Desktop", "Documents"]
home = os.path.expanduser("~")
SKIP = {".git","node_modules",".Trash","Library","__pycache__",".venv","venv","DerivedData",".next"}

def is_dl(st):
    return bool(getattr(st, "st_flags", 0) & UF_DATALESS)

def brctl(p):
    if not os.path.isfile("/usr/bin/brctl"):
        return
    try:
        subprocess.run(["/usr/bin/brctl","download",p], stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, timeout=8, check=False)
    except Exception:
        pass

def force_read(p, t):
    code = "import sys\np=sys.argv[1]\nopen(p,'rb').read(16384)\n"
    try:
        r = subprocess.run([sys.executable,"-c",code,p], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=t, check=False)
        return r.returncode == 0
    except Exception:
        return False

total_mat = total_fail = total_rem = 0
for rel in roots:
    root = os.path.join(home, rel) if not os.path.isabs(rel) else rel
    if not os.path.isdir(root):
        print(f"ROOT {rel} missing")
        continue
    brctl(root)
    t0 = time.time()
    mat = fail = found = 0
    for dp, dns, fns in os.walk(root):
        dns[:] = [d for d in dns if d not in SKIP]
        if time.time() - t0 > max_sec:
            break
        for name in fns:
            if time.time() - t0 > max_sec:
                break
            p = os.path.join(dp, name)
            try:
                st = os.lstat(p)
            except OSError:
                continue
            if not stat.S_ISREG(st.st_mode) or not is_dl(st):
                continue
            found += 1
            brctl(p)
            if force_read(p, timeout_per):
                mat += 1
            else:
                fail += 1
            if (mat + fail) % 10 == 0:
                print(f"  {rel} progress mat={mat} fail={fail} found={found}", flush=True)
    rem = 0
    for dp, dns, fns in os.walk(root):
        dns[:] = [d for d in dns if d not in SKIP]
        for name in fns:
            p = os.path.join(dp, name)
            try:
                st = os.lstat(p)
            except OSError:
                continue
            if stat.S_ISREG(st.st_mode) and is_dl(st):
                rem += 1
    total_mat += mat
    total_fail += fail
    total_rem += rem
    print(f"ROOT {rel} found={found} mat={mat} fail={fail} remaining={rem} sec={int(time.time()-t0)}", flush=True)
print(f"TOTAL mat={total_mat} fail={total_fail} remaining={total_rem}", flush=True)
"""


def default_icloud_roots(home: Path | None = None) -> list[Path]:
    """Paths under Home that are commonly iCloud File Provider folders."""
    h = Path(home) if home else Path.home()
    roots = []
    for name in ("Desktop", "Documents"):
        p = h / name
        if p.is_dir():
            roots.append(p)
    return roots
