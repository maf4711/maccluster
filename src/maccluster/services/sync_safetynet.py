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


def new_run_dir(root: Path | None = None) -> Path:
    base = root or default_safetynet_root()
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = base / stamp
    path.mkdir(parents=True, exist_ok=True)
    return path


def prune_old_runs(root: Path | None = None, *, keep: int = DEFAULT_KEEP_RUNS) -> int:
    """Delete all but the `keep` most recent SafetyNet run directories.

    Run directories are named by their UTC creation stamp (new_run_dir), so
    lexicographic order is chronological order. Anything not matching that
    stamp pattern is left alone. Returns the number of directories removed.
    """
    base = root or default_safetynet_root()
    if not base.is_dir():
        return 0
    runs = sorted(
        (p for p in base.iterdir() if p.is_dir() and _RUN_DIR_NAME.match(p.name)),
        key=lambda p: p.name,
        reverse=True,
    )
    removed = 0
    for stale in runs[keep:]:
        shutil.rmtree(stale, ignore_errors=True)
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
