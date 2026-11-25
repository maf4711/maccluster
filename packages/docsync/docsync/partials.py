"""Where rsync keeps partially transferred files, and cleanup of the ones older versions left behind.

Until 1.3.5 rsync ran with `--partial-dir=.docsync-partial` (relative), so every failed transfer was
kept inside the destination directory — inside the synced root, where iCloud Drive uploaded it
(incident 2026-11-25: 23,275 zero-filled files, 47 GB). Now the partial dir is an absolute path in
docsync's state dir on the receiving Mac (one per root and peer), never inside a root.

`sweep()` moves stale `.docsync-partial` dirs of older versions out of a root — into the root's
SafetyNet, never unlinked — but only when every file in them is all zero bytes (or empty);
a dir holding any real data, a symlink or an iCloud-evicted file stays and is reported.
"""

from __future__ import annotations

import fnmatch
import os
import re
import time
from pathlib import Path

from docsync.manifest import SF_DATALESS
from docsync.safetynet import SN_DIR

PARTIAL_NAME = ".docsync-partial"
TMP = "tmp"  # rsync --temp-dir below the partial dir
CHUNK = 1 << 20
MAX_AGE_DAYS = 7.0  # partial files in the state dir older than this are dropped (prune)


def key(root_name: str, peer: str) -> str:
    """Per root and peer, so two peers syncing one root in parallel never share a partial dir."""
    return re.sub(r"[^A-Za-z0-9._-]", "_", f"{root_name}-{peer}")


def local_dir(state_dir: Path, root_name: str, peer: str) -> Path:
    return Path(os.path.abspath(os.path.expanduser(str(state_dir)))) / "partial" / key(root_name, peer)


def remote_script(peer_key: str, root_expr: str) -> str:
    """sh for the peer: create its partial dir (peer_key from key()) and its tmp/ in its docsync
    state dir; print the absolute dir, the real root path, the device of each (stat -f %d, macOS)."""
    return ('S="${DOCSYNC_STATE_DIR:-$HOME/.local/state/docsync}"; case "$S" in "~/"*) S="$HOME/${S#\\~/}";; esac; '
            f'D="$S/partial/{peer_key}"; mkdir -p "$D/{TMP}" && cd "$D" && pwd -P && (cd {root_expr} && pwd -P) '
            f'&& stat -f %d "$D" && stat -f %d {root_expr}')


def same_device(a, b) -> bool:
    try:
        return os.stat(a).st_dev == os.stat(b).st_dev
    except OSError:
        return False


def inside(path, root) -> bool:
    """path is root or lies below it, after resolving symlinks (both on this Mac)."""
    p, r = os.path.realpath(os.path.expanduser(str(path))), os.path.realpath(os.path.expanduser(str(root)))
    return p == r or p.startswith(r.rstrip("/") + "/")


def rsync_args(d: str | None, same_dev: bool) -> list[str]:
    """--partial-dir=d, plus --temp-dir=d/tmp when d is on the receiving root's volume (rsync's
    .<name>.XXXXXX temp files then never appear in the root, and the final rename stays atomic)."""
    if not d:
        return []
    return [f"--partial-dir={d}"] + ([f"--temp-dir={d}/{TMP}"] if same_dev else [])


def _all_zero(d: Path) -> bool:
    """Every file below d is a regular, materialised file whose bytes are all 0x00 (or it is empty)."""
    for dirpath, _dirs, files in os.walk(d):
        for f in files:
            full = os.path.join(dirpath, f)
            try:
                st = os.lstat(full)
                if not os.path.isfile(full) or os.path.islink(full) or getattr(st, "st_flags", 0) & SF_DATALESS:
                    return False  # an evicted file is never read here: that would download it
                with open(full, "rb") as fh:
                    while chunk := fh.read(CHUNK):
                        if chunk.count(0) != len(chunk):
                            return False
            except OSError:
                return False
    return True


def _target(root: Path, stamp: str, rel: str) -> Path:
    dst, n = root / SN_DIR / stamp / rel, 1
    while dst.exists():
        dst, n = root / SN_DIR / stamp / f"{rel}.{n}", n + 1
    return dst


def sweep(root: Path, stamp: str, excludes: list[str] | None = None) -> tuple[list[str], list[str]]:
    """(moved, kept) relative paths of stale partial dirs below root. Moved dirs go to
    <root>/.docsync-safetynet/<stamp>/<relpath> (SafetyNet retention prunes them later).
    `excludes`: directory-name patterns not descended into (the SafetyNet is never)."""
    root = Path(root)
    skip = [p for p in (excludes or []) if "/" not in p]
    moved: list[str] = []
    kept: list[str] = []
    for dirpath, dirs, _files in os.walk(root):
        found = [d for d in dirs if d == PARTIAL_NAME]
        dirs[:] = [d for d in dirs if d != PARTIAL_NAME and d != SN_DIR
                   and not any(fnmatch.fnmatchcase(d, p) for p in skip)]
        for d in found:
            full = Path(dirpath) / d
            rel = full.relative_to(root).as_posix()
            if full.is_symlink() or not _all_zero(full):
                kept.append(rel)
                continue
            dst = _target(root, stamp, rel)
            dst.parent.mkdir(parents=True, exist_ok=True)
            os.rename(full, dst)
            moved.append(rel)
    return moved, kept


def prune(base: Path, max_age_days: float = MAX_AGE_DAYS, now: float | None = None) -> int:
    """Remove files below the state dir's partial/ (never a root) whose newest of mtime/ctime is
    older than max_age_days (rsync may give a partial file its source's mtime; the move into the
    partial dir sets ctime). Directories stay: a parallel peer may be using its tmp/. Returns the number of files removed."""
    cutoff = (time.time() if now is None else now) - max_age_days * 86400
    removed = 0
    for dirpath, _dirs, files in os.walk(base):
        for f in files:
            full = os.path.join(dirpath, f)
            try:
                st = os.lstat(full)
                if max(st.st_mtime, st.st_ctime) < cutoff:
                    os.unlink(full)
                    removed += 1
            except OSError:
                pass
    return removed
