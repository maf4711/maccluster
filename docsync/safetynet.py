"""SafetyNet: copy every file docsync is about to overwrite into
<root>/.docsync-safetynet/<UTC stamp>/<relpath> on the side being overwritten,
then keep that directory within a retention policy (count, age, size).

`.docsync*` is excluded from every manifest, so backups never sync themselves.
On APFS the copy is a clone (clonefile): instant and free until either side changes.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import os
import shlex
import shutil
from datetime import datetime, timezone
from pathlib import Path

SN_DIR = ".docsync-safetynet"
STAMP_FMT = "%Y%m%dT%H%M%SZ"

# One retention implementation, executed locally via exec() and on the peer as `python3 -`
# (peer Python is 3.9, so no match/walrus/3.10+ syntax in here).
PRUNE_PY = r'''
import calendar, os, re, shutil, sys, time
RUN = re.compile(r"^\d{8}T\d{6}Z$")


def runs(base):
    if not os.path.isdir(base):
        return []
    return sorted((n for n in os.listdir(base) if RUN.match(n) and os.path.isdir(os.path.join(base, n))), reverse=True)


def dir_bytes(path, seen=None):
    """Each inode once (hardlinked or repeated files are not double counted)."""
    seen = set() if seen is None else seen
    total = 0
    for dirpath, _dirs, files in os.walk(path):
        for f in files:
            full = os.path.join(dirpath, f)
            try:
                st = os.lstat(full)
            except OSError:
                continue
            if os.path.islink(full) or (st.st_dev, st.st_ino) in seen:
                continue
            seen.add((st.st_dev, st.st_ino))
            total += st.st_size
    return total


def age_days(name, now):
    return (now - calendar.timegm(time.strptime(name, "%Y%m%dT%H%M%SZ"))) / 86400.0


def prune(base, keep=5, max_age_days=14.0, max_bytes=20 * 1024 ** 3, now=None):
    """Newest first; drop beyond `keep`, then older than `max_age_days`, then oldest until
    the total is <= `max_bytes`. The newest run is never removed by age or size."""
    now = time.time() if now is None else now
    names = runs(base)
    removed = 0
    for n in names[keep:]:
        shutil.rmtree(os.path.join(base, n), ignore_errors=True)
        removed += 1
    names = names[:keep]
    if max_age_days is not None:
        kept = []
        for i, n in enumerate(names):
            if i > 0 and age_days(n, now) > max_age_days:
                shutil.rmtree(os.path.join(base, n), ignore_errors=True)
                removed += 1
            else:
                kept.append(n)
        names = kept
    if max_bytes is not None and names:
        sizes = dict((n, dir_bytes(os.path.join(base, n))) for n in names)
        total = sum(sizes.values())
        for n in reversed(names):
            if total <= max_bytes or n == names[0]:
                break
            shutil.rmtree(os.path.join(base, n), ignore_errors=True)
            total -= sizes[n]
            removed += 1
    return removed


def usage(base):
    return [(n, dir_bytes(os.path.join(base, n))) for n in runs(base)]


if __name__ == "__main__" and len(sys.argv) >= 6:
    a = sys.argv
    n = prune(os.path.expanduser(a[1]), int(a[2]), None if float(a[3]) < 0 else float(a[3]),
              None if int(a[4]) < 0 else int(a[4]), float(a[5]))
    sys.stdout.write("REMOVED %d\n" % n)
'''

_ns: dict = {"__name__": "docsync_safetynet_prune"}
exec(compile(PRUNE_PY, "docsync/safetynet.py:PRUNE_PY", "exec"), _ns)
prune = _ns["prune"]
usage = _ns["usage"]
runs = _ns["runs"]


def stamp_now() -> str:
    return datetime.now(timezone.utc).strftime(STAMP_FMT)


_libc = None


def _clone(src: Path, dst: Path) -> None:
    """APFS clonefile (keeps data blocks shared, copies xattrs/ACLs); plain copy elsewhere."""
    global _libc
    if _libc is None:
        try:
            _libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
            _libc.clonefile.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_int]
        except (OSError, AttributeError):
            _libc = False
    if _libc and _libc.clonefile(os.fsencode(src), os.fsencode(dst), 0) == 0:
        return
    shutil.copy2(src, dst)


def backup_local(root: Path, paths: list[str], stamp: str) -> tuple[int, int]:
    """(files, bytes) copied. Raises OSError when a backup cannot be written — the caller then
    aborts the transfer before anything is overwritten."""
    n = nbytes = 0
    base = root / SN_DIR / stamp
    for rel in paths:
        src = root / rel
        if src.is_symlink() or not src.is_file():
            continue
        dst = base / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        _clone(src, dst)
        n += 1
        nbytes += src.stat().st_size
    return n, nbytes


def remote_backup_script(root_expr: str, paths: list[str], stamp: str) -> str:
    """sh script for the peer (fed via `sh -s`): clone-copy each existing path, print 'B <size>'."""
    lines = ["set -e", f"cd {root_expr}", f"S={SN_DIR}/{stamp}"]
    for p in paths:
        q = shlex.quote(p)
        d = shlex.quote(os.path.dirname(p) or ".")
        lines.append(f'if [ -f {q} ] && [ ! -L {q} ]; then mkdir -p "$S"/{d}; '
                     f'cp -c -p {q} "$S"/{q} 2>/dev/null || cp -p {q} "$S"/{q}; echo "B $(stat -f %z {q})"; fi')
    return "\n".join(lines) + "\n"


def parse_backup_output(text: str) -> tuple[int, int]:
    sizes = [int(line[2:]) for line in text.splitlines() if line.startswith("B ") and line[2:].strip().isdigit()]
    return len(sizes), sum(sizes)


def status_lines(root: Path, policy: tuple) -> list[str]:
    keep, age, max_bytes = policy
    base = str(root / SN_DIR)
    u = usage(base)
    total = sum(b for _, b in u)
    over = len(u) > keep or (max_bytes is not None and total > max_bytes)
    lines = [f"   {root}/{SN_DIR}: {len(u)} runs, {total / 1024**3:.2f} GB"
             f"{'  ⚠ over policy' if over else ''}"]
    for name, b in u:
        lines.append(f"      {name}  {b / 1024**2:10,.1f} MB")
    return lines
