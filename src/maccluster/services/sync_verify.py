"""Post-sync verification sample (size + mtime)."""

from __future__ import annotations

import stat
from pathlib import Path
from typing import Any


def verify_local_sample(
    local_home: Path,
    expected: dict[str, Any],
    rels: list[str],
    *,
    sample: int = 20,
) -> tuple[bool, int, int, list[str]]:
    """
    Check up to ``sample`` paths exist under local_home with size match.
    mtime may drift by 1s on some FS — allow ±2s.

    Returns (ok, checked, mismatches, sample_mismatch_paths).
    """
    if sample <= 0 or not rels:
        return True, 0, 0, []
    take = rels[:sample]
    checked = 0
    mismatches = 0
    bad: list[str] = []
    for rel in take:
        if rel not in expected:
            continue
        exp = expected[rel]
        path = local_home / rel
        checked += 1
        try:
            st = path.lstat()
        except OSError:
            mismatches += 1
            bad.append(rel)
            continue
        if not (stat.S_ISREG(st.st_mode) or stat.S_ISLNK(st.st_mode)):
            mismatches += 1
            bad.append(rel)
            continue
        # size: regular files only
        if stat.S_ISREG(st.st_mode) and st.st_size != exp.size:
            mismatches += 1
            bad.append(rel)
            continue
        # mtime: allow 2s drift
        if abs(st.st_mtime_ns - exp.mtime_ns) > 2_000_000_000:
            # still size-ok; soft warn only if size matched — count as mismatch for safety
            mismatches += 1
            bad.append(rel)
    return mismatches == 0, checked, mismatches, bad[:10]


def remote_stat_sample_script() -> str:
    """Python one-liner body: read list of rels, print rel\\tmtime\\tsize for existing."""
    return """\
import os, stat, sys
home = sys.argv[1]
for rel in sys.stdin.read().splitlines():
    rel = rel.strip()
    if not rel or ".." in rel.split("/"):
        continue
    p = os.path.join(home, rel)
    try:
        st = os.lstat(p)
    except OSError:
        print(f"{rel}\\tMISSING\\t0")
        continue
    print(f"{rel}\\t{st.st_mtime_ns}\\t{st.st_size}")
"""
