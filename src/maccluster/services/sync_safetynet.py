"""SafetyNet-lite: backup local files before overwrite (pull)."""

from __future__ import annotations

import re
import shutil
from datetime import UTC, datetime
from pathlib import Path

from maccluster.config.paths import default_safetynet_root
from maccluster.errors import CliError
from maccluster.services.sync_paths import contained_path

_RUN_DIR_NAME = re.compile(r"^\d{8}T\d{6}Z$")

DEFAULT_KEEP_RUNS = 5
DEFAULT_MAX_AGE_DAYS = 14
DEFAULT_MAX_BYTES = 20 * 1024**3  # 20 GiB across all runs; the newest run is always kept


def _run_dirs(base: Path) -> list[Path]:
    """SafetyNet run directories, newest first (names are UTC stamps)."""
    return sorted(
        (p for p in base.iterdir() if p.is_dir() and _RUN_DIR_NAME.match(p.name)),
        key=lambda p: p.name,
        reverse=True,
    )


def _dir_bytes(path: Path, seen: set[tuple[int, int]] | None = None) -> int:
    """Bytes on disk-ish: each inode once. SafetyNet runs share files via hardlinks
    (staging), so summing st_size per path counted node-a's 274 GB as 1.6 TB."""
    seen = set() if seen is None else seen
    total = 0
    for p in path.rglob("*"):
        try:
            st = p.lstat()
        except OSError:
            continue
        if not p.is_file() or p.is_symlink():
            continue
        key = (st.st_dev, st.st_ino)
        if key in seen:
            continue
        seen.add(key)
        total += st.st_size
    return total


def _run_age_days(path: Path, now: datetime) -> float:
    try:
        stamp = datetime.strptime(path.name, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
    except ValueError:
        return 0.0
    return (now - stamp).total_seconds() / 86400.0


def safetynet_usage(root: Path | None = None) -> tuple[int, int]:
    """(run directories, bytes) under the SafetyNet root."""
    base = root or default_safetynet_root()
    if not base.is_dir():
        return 0, 0
    runs = _run_dirs(base)
    seen: set[tuple[int, int]] = set()
    return len(runs), sum(_dir_bytes(r, seen) for r in runs)


def new_run_dir(root: Path | None = None) -> Path:
    base = root or default_safetynet_root()
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = base / stamp
    path.mkdir(parents=True, exist_ok=True)
    return path


def prune_old_runs(
    root: Path | None = None,
    *,
    keep: int = DEFAULT_KEEP_RUNS,
    max_age_days: float | None = DEFAULT_MAX_AGE_DAYS,
    max_bytes: int | None = DEFAULT_MAX_BYTES,
    now: datetime | None = None,
) -> int:
    """Delete SafetyNet run directories beyond the retention policy.

    Three limits, applied in this order, oldest runs first; the newest run is
    never removed by the age or size limit:
      1. count — keep at most `keep` runs,
      2. age   — drop runs older than `max_age_days` (None disables),
      3. size  — drop oldest runs until the total is <= `max_bytes` (None disables).
    Run directories are named by their UTC creation stamp (new_run_dir), so
    lexicographic order is chronological order. Anything not matching that
    stamp pattern is left alone. Returns the number of directories removed.
    """
    base = root or default_safetynet_root()
    if not base.is_dir():
        return 0
    runs = _run_dirs(base)
    removed = 0
    for stale in runs[keep:]:
        shutil.rmtree(stale, ignore_errors=True)
        removed += 1
    runs = runs[:keep]
    now = now or datetime.now(UTC)
    if max_age_days is not None:
        kept = []
        for i, run in enumerate(runs):
            if i > 0 and _run_age_days(run, now) > max_age_days:
                shutil.rmtree(run, ignore_errors=True)
                removed += 1
            else:
                kept.append(run)
        runs = kept
    if max_bytes is not None and runs:
        sizes = {r: _dir_bytes(r) for r in runs}
        total = sum(sizes.values())
        for run in reversed(runs):  # oldest first
            if total <= max_bytes or run is runs[0]:
                break
            shutil.rmtree(run, ignore_errors=True)
            total -= sizes[run]
            removed += 1
    return removed


def backup_before_overwrite(
    local_home: Path,
    rels: list[str],
    *,
    run_dir: Path,
    abs_ditto: str | None = None,
    runner=None,
    timeout: float = 120.0,
) -> int:
    """
    Copy existing local files that will be overwritten by pull into SafetyNet.

    Returns number of files backed up. Copies must own their data: a hardlink
    would be overwritten together with the original by an in-place transfer.
    """
    n = 0
    for rel in rels:
        src = contained_path(local_home, rel)
        if not src.exists() and not src.is_symlink():
            continue
        dst = contained_path(run_dir, rel)
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists() or dst.is_symlink():
            dst.unlink()
        ok = False
        try:
            if src.is_symlink():
                dst.symlink_to(src.readlink())
                ok = True
            elif abs_ditto and runner is not None:
                r = runner.run([abs_ditto, str(src), str(dst)], timeout=min(timeout, 60.0))
                ok = r.returncode == 0
            if not ok:
                shutil.copy2(src, dst, follow_symlinks=False)
                ok = True
        except OSError as exc:
            raise CliError(f"SafetyNet backup failed for {rel!r}: {exc}", exit_code=1) from exc
        if ok:
            n += 1
    return n
