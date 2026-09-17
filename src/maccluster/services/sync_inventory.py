"""Local side of the home sync: the walk, exclude matching, completeness.

``FileMeta`` is the per-file record (mtime_ns + size) that both inventories
produce and the planner consumes; the peer-side walk lives in
``sync_inventory_remote``. The local walk lists through one pooled killable
helper (``sync_scandir``) and reports whether it saw the whole tree — a
truncated walk must not drive a newest-wins bidirectional plan.
"""

from __future__ import annotations

import os
import stat
import time
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path

from maccluster.errors import CliError
from maccluster.render.progress import NullProgress, ProgressLike
from maccluster.services.sync_scandir import (
    REASON_TIMEOUT,
    REASON_UNREADABLE,  # noqa: F401 — re-exported for callers/tests
    REASON_WORKER,
    ScandirWorker,
)


@dataclass(frozen=True)
class FileMeta:
    mtime_ns: int
    size: int


class LocalInventory(dict[str, FileMeta]):
    """``rel -> FileMeta`` plus how much of the tree the walk actually saw.

    It *is* the plain mapping every consumer already expects; the extra
    attributes exist because a truncated walk is not a diff. ``plan_transfers``
    is newest-wins bidirectional, so a file the walk never reached looks exactly
    like a file that only exists on the peer — and gets pulled back over the
    local copy. Callers must refuse to drive a real transfer while ``partial``
    is set (see ``sync_home``).

    ``partial`` means coverage was cut short (time budget, or a directory that
    hung and was killed). A directory that is merely unreadable is listed in
    ``skipped_dirs`` but does not set ``partial``: that is a stable property of
    the tree, not a truncation, and would otherwise block every run.
    """

    partial: bool = False
    partial_reason: str = ""
    skipped_dirs: tuple[str, ...] = ()


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
# Skipped directories are named in the result; cap the list so a pathological
# tree cannot turn the report (and the run log) into a wall of paths.
_MAX_REPORTED_SKIPS = 50


def _safe_scandir(
    path: Path | str,
    *,
    timeout_s: float = 6.0,
) -> list[tuple[str, str, bool, bool]] | None:
    """One killable directory listing — iCloud/FP hangs ignore SIGALRM.

    Compat shim for single lookups. A *walk* must not use this: it spawns a
    helper per call, which is exactly the per-directory interpreter start the
    pooled ``ScandirWorker`` exists to avoid.
    """
    with ScandirWorker(timeout_s=timeout_s) as worker:
        return worker.listdir(path)


def inventory_local(
    root: Path,
    excludes: tuple[str, ...],
    includes: tuple[str, ...] = (),
    *,
    progress: ProgressLike | None = None,
    max_sec: float | None = None,
    dir_sec: float | None = None,
) -> LocalInventory:
    """Walk home (or only ``includes`` roots); regular files + symlinks only.

    Hang-safe for iCloud Desktop/Documents: the listings run in one pooled,
    killable helper (``ScandirWorker``) instead of a child per directory. Fast
    ``os.walk`` for Developer/Downloads/.ssh/.config. Skips ``UF_DATALESS``.
    When *includes* is set, only those subtrees are walked. Optional *progress*
    reports live file counts so the bar is not stuck at 0%.

    The result is a ``LocalInventory``: still a ``rel -> FileMeta`` mapping, now
    also carrying whether the walk was cut short and which directories it had
    to skip.
    """
    prog = progress or NullProgress()
    out = LocalInventory()
    skipped: list[str] = []
    hung_dirs = 0
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

    def _record_skip(cur: str, reason: str) -> None:
        """Name every directory the walk could not read — never drop one silently."""
        nonlocal hung_dirs
        try:
            rel_h = os.path.relpath(cur, root).replace("\\", "/")
        except Exception:
            rel_h = cur
        if reason == REASON_TIMEOUT or reason == REASON_WORKER:
            hung_dirs += 1
            prog.note(f"  skip-hang local: {rel_h}")
        else:
            prog.note(f"  skip-unreadable local: {rel_h}")
        if len(skipped) < _MAX_REPORTED_SKIPS:
            skipped.append(rel_h)

    def _safe_walk(worker: ScandirWorker, start: str, label: str) -> None:
        """Killable scandir walk for iCloud Desktop/Documents (pooled helper)."""
        stack = [start]
        while stack and _budget_ok():
            cur = stack.pop()
            entries = worker.listdir(cur)
            if entries is None:
                _record_skip(cur, worker.last_reason)
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

    # One helper for the whole walk: interpreter startup is paid once, not once
    # per directory (the ~28x regression this replaced).
    budget_hit = False
    worker = ScandirWorker(timeout_s=dir_s)
    try:
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
                    kids = worker.listdir(p0)
                    if kids is None:
                        _record_skip(p0, worker.last_reason)
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
                budget_hit = True
                prog.note("  local inventory time budget reached (partial)")
                break
            prog.note(f"  local walk: {label}")
            _tick(label)
            if safe_mode:
                _safe_walk(worker, walk_path, label)
            else:
                _fast_walk(Path(walk_path), label)
    finally:
        worker.close()

    if not _budget_ok():
        budget_hit = True
        prog.note(f"  local inventory partial: {len(out)} files (budget {int(max_s)}s)")

    reasons: list[str] = []
    if budget_hit:
        reasons.append(f"time budget {int(max_s)}s reached")
    if hung_dirs:
        reasons.append(f"{hung_dirs} directories timed out and were skipped")
    out.partial = bool(reasons)
    out.partial_reason = "; ".join(reasons)
    out.skipped_dirs = tuple(skipped)
    if out.partial:
        prog.note(f"  local inventory INCOMPLETE: {out.partial_reason}")
    return out


def describe_partial(inv: dict[str, FileMeta]) -> str:
    """One line saying why a local walk is incomplete — "" when it is complete."""
    if not getattr(inv, "partial", False):
        return ""
    detail = str(getattr(inv, "partial_reason", "") or "walk truncated")
    skipped = tuple(getattr(inv, "skipped_dirs", ()) or ())
    if skipped:
        more = f" (+{len(skipped) - 3} more)" if len(skipped) > 3 else ""
        detail += f"; skipped: {', '.join(skipped[:3])}{more}"
    return detail


def guard_partial_inventory(
    inv: dict[str, FileMeta],
    *,
    dry_run: bool,
    allow_partial: bool,
) -> str:
    """Refuse to drive a real transfer from a walk that never saw the whole tree.

    ``plan_transfers`` is newest-wins *bidirectional*: a local file the walk
    never reached is indistinguishable from a file that only exists on the
    peer, so it gets pulled back — silently, and reported as success. Returns
    the note to display (empty when the inventory is complete) and raises
    ``CliError`` when a real run would otherwise plan from a truncated view.
    """
    detail = describe_partial(inv)
    if not detail:
        return ""
    if dry_run:
        return f"PARTIAL local inventory ({detail}) — the plan below is incomplete"
    if allow_partial:
        return f"PARTIAL local inventory ({detail}) — proceeding (--allow-partial-inventory)"
    raise CliError(
        f"local inventory is PARTIAL ({detail}). Refusing to transfer: files the "
        "walk never reached look like 'only on the peer' to a newest-wins sync and "
        "would be pulled back over newer local copies. Narrow the scope with "
        "--include/--preset, raise MACCLUSTER_INV_MAX_SEC, or pass "
        "--allow-partial-inventory to accept that risk.",
        exit_code=1,
    )


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
