"""Engine switch for `sync home|dev`: docsync (three-way) or ditto (newest-wins copy).

Callers (sync command, automation) use :func:`sync_tree`; it prepares the same inputs as
``sync_home`` (cluster peers, Keychain user, presets, excludes) and hands them to docsync, or
calls ``sync_home`` unchanged for the ditto engine. ``sync_service`` itself is not modified.
"""

from __future__ import annotations

import getpass
import os
import sys
from dataclasses import replace
from pathlib import Path

from maccluster.app_factory import AppContext
from maccluster.domain.models import SyncHomeResult
from maccluster.errors import CliError
from maccluster.render.progress import NullProgress
from maccluster.services import sync_docsync


def sync_tree(
    ctx: AppContext, *, engine: str | None = None, force_deletes: bool = False, **kwargs
) -> SyncHomeResult:
    """`sync_home` keyword arguments plus ``engine`` (auto|docsync|ditto) and ``force_deletes``."""
    from maccluster.services.sync_service import normalize_sync_target, sync_home

    policy = (kwargs.get("conflict_policy") or "newer").strip().lower()
    reasons = sync_docsync.ditto_only_reasons(
        push_only=bool(kwargs.get("push_only")),
        pull_only=bool(kwargs.get("pull_only")),
        transport=kwargs.get("transport"),
        conflict_policy=policy,
        force_icloud=bool(kwargs.get("force_icloud")),
        identical=bool(kwargs.get("identical")),
        quick=bool(kwargs.get("quick")),
        target=normalize_sync_target(kwargs.get("target", "home")) or "home",
        max_files=kwargs.get("max_files"),
        max_bytes=kwargs.get("max_bytes"),
    )
    chosen, note = sync_docsync.resolve_engine(engine, reasons)
    prog = kwargs.get("progress") or NullProgress()
    if note:
        prog.note(note)
    if chosen == "ditto":
        return sync_home(ctx, **kwargs)
    return _sync_docsync(ctx, prog=prog, policy=policy, force_deletes=force_deletes, **kwargs)


def _sync_docsync(
    ctx: AppContext, *, prog, policy: str, force_deletes: bool, **kw
) -> SyncHomeResult:
    from maccluster.config.paths import default_sync_exclude_file
    from maccluster.constants import (
        SYNC_DEFAULT_PRESETS,
        SYNC_DEV_EXCLUDES,
        SYNC_HOME_EXCLUDES,
        TIMEOUT_SYNC,
    )
    from maccluster.services.config_service import load_and_bind_self
    from maccluster.services.sync_filters import load_exclude_file, merge_includes
    from maccluster.services.sync_history import write_run_log
    from maccluster.services.sync_service import (
        _resolve_peers,
        normalize_sync_target,
        resolve_sync_tree,
    )

    target = normalize_sync_target(kw.get("target", "home")) or "home"
    cfg, self_node = load_and_bind_self(ctx)
    try:
        from maccluster.services.keychain_service import resolve_ssh_user

        user = resolve_ssh_user(ctx, explicit=kw.get("user"))
    except Exception:
        user = (kw.get("user") or os.environ.get("USER") or getpass.getuser() or "").strip()
    if not user:
        raise CliError("cannot determine local username for SSH", exit_code=1)
    local_home = (
        Path(kw["home"]).expanduser() if kw.get("home") else resolve_sync_tree(target, None)
    )
    if not local_home.is_dir():
        raise CliError(f"not a directory: {local_home}", exit_code=1)
    remote_home = str(kw.get("remote_home") or local_home)

    exclude_from = kw.get("exclude_from")
    excludes = (
        tuple(SYNC_HOME_EXCLUDES)
        + (SYNC_DEV_EXCLUDES if target == "dev" else ())
        + load_exclude_file(Path(exclude_from) if exclude_from else default_sync_exclude_file())
        + tuple(kw.get("extra_excludes") or ())
    )
    includes = merge_includes(kw.get("presets") or (), kw.get("includes") or ())
    if target == "home" and not includes and not kw.get("full_home"):
        includes = merge_includes(SYNC_DEFAULT_PRESETS, ())
        prog.note("default scope: " + ", ".join(includes) + "  (pass --full-home for entire $HOME)")
    elif kw.get("full_home") and includes:
        raise CliError("use either --full-home or --preset/--include, not both", exit_code=2)
    includes, dropped = sync_docsync.drop_git_trees(includes)
    if dropped:
        prog.note(
            "docsync engine skips git trees and machine state: "
            + ", ".join(dropped)
            + "  (sync repos with git, or `maccluster sync dev --engine ditto`)"
        )
    if kw.get("full_home"):
        excludes = excludes + tuple(
            f"{t}/" for t in (*sync_docsync.GIT_TREES, *sync_docsync.MACHINE_STATE)
        )
    if target == "home" and not includes and not kw.get("full_home"):
        raise CliError(
            "nothing to sync with the docsync engine after leaving out git trees and machine state",
            exit_code=2,
        )

    peers = _resolve_peers(
        cfg.nodes,
        self_node,
        peer_filter=kw.get("peer"),
        default_user=user,
        peer_limit=kw.get("peer_limit"),
    )
    via = "wifi" if kw.get("transport") == "wifi" or (kw.get("via") or "tb") == "wifi" else "tb"
    result = run_with_deadline(
        prog,
        kw.get("timeout"),
        lambda: sync_docsync.run(
            prog=prog,
            local_home=local_home,
            remote_home=remote_home,
            peers=peers,
            self_ip=str(self_node.ip),
            via=via,
            target=target,
            includes=includes,
            excludes=excludes,
            conflict_policy=policy,
            dry_run=bool(kw.get("dry_run")),
            compare_only=bool(kw.get("compare_only")),
            default_user=user,
            force_deletes=force_deletes,
            max_runtime=float(kw.get("timeout") or TIMEOUT_SYNC),
        ),
    )
    if kw.get("write_log", True) and not result.dry_run:
        try:
            result = replace(result, log_path=str(write_run_log(result)))
        except Exception as exc:  # a log problem never fails the sync
            prog.note(f"warning: run log not written: {exc}")
    return result


def _load_watchdog(prog):
    """docsync's process watchdog (docsync-lan >= 1.2), or None with a note for older engines."""
    try:
        from docsync import watchdog  # noqa: PLC0415 — optional engine
    except ImportError:
        prog.note(f"docsync {sync_docsync.docsync_version() or '?'} has no watchdog; no deadline")
        return None
    return watchdog


def _dump_stacks(wd) -> Path | None:
    """All thread stacks to ~/Library/Logs/maccluster/deadline-<UTC stamp>.txt: docsync's
    `watchdog.dump_stacks` when it has one, else faulthandler. None if nothing could be written."""
    import faulthandler
    import time

    path = Path.home() / "Library" / "Logs" / "maccluster"
    path = path / f"deadline-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}.txt"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        dump = getattr(wd, "dump_stacks", None)
        if dump is not None:
            dump(path)
        else:
            with path.open("w") as fh:
                faulthandler.dump_traceback(fh, all_threads=True)
        return path
    except Exception:  # the exit must happen even when the dump fails
        return None


def run_with_deadline(prog, timeout: float | None, fn):
    """Run `fn` with the whole process bounded by `timeout` seconds (default TIMEOUT_SYNC).
    On expiry this process's rsync/ssh children are ended and the process exits 4, which also
    releases the docsync run lock (2026-11-07: a launchd sync slept 12 min holding it)."""
    from maccluster.constants import TIMEOUT_SYNC

    seconds = float(timeout or TIMEOUT_SYNC)
    wd = _load_watchdog(prog)
    if wd is None:
        return fn()

    def expire() -> None:
        stacks = _dump_stacks(wd)
        try:
            print(
                f"maccluster: deadline of {seconds:g} s exceeded — ending this sync (exit {wd.EXIT_DEADLINE})"
                + (f"; thread stacks in {stacks}" if stacks else ""),
                file=sys.stderr,
                flush=True,
            )
        except Exception:  # noqa: BLE001 — a broken pipe must not stop the exit
            pass
        wd.kill_descendants()
        os._exit(wd.EXIT_DEADLINE)

    wd.arm(seconds, expire)
    try:
        return fn()
    finally:
        wd.disarm()
