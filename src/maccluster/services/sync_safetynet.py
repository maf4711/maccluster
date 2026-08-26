"""SafetyNet-lite: backup local files before overwrite (pull)."""

from __future__ import annotations

import shutil
from datetime import UTC, datetime
from pathlib import Path

from maccluster.config.paths import default_safetynet_root


def new_run_dir(root: Path | None = None) -> Path:
    base = root or default_safetynet_root()
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = base / stamp
    path.mkdir(parents=True, exist_ok=True)
    return path


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

    Returns number of files backed up. Uses hardlink/ditto when possible;
    falls back to shutil.copy2.
    """
    n = 0
    for rel in rels:
        if ".." in rel.split("/"):
            continue
        src = local_home / rel
        if not src.exists() and not src.is_symlink():
            continue
        dst = run_dir / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists() or dst.is_symlink():
            try:
                dst.unlink()
            except OSError:
                pass
        ok = False
        try:
            import os

            os.link(src, dst)
            ok = True
        except OSError:
            if abs_ditto and runner is not None:
                r = runner.run([abs_ditto, str(src), str(dst)], timeout=min(timeout, 60.0))
                ok = r.returncode == 0
            if not ok:
                try:
                    if src.is_symlink():
                        dst.symlink_to(src.readlink())
                    else:
                        shutil.copy2(src, dst, follow_symlinks=False)
                    ok = True
                except OSError:
                    ok = False
        if ok:
            n += 1
    return n
