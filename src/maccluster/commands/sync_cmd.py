"""sync command — home directory sync (CCC-inspired options)."""

from __future__ import annotations

import sys

from maccluster.app_factory import AppContext
from maccluster.constants import TIMEOUT_SYNC
from maccluster.errors import CliError
from maccluster.render.json_out import dumps, to_jsonable
from maccluster.render.progress import NullProgress, SyncProgress
from maccluster.services.sync_history import format_last_run, read_last_run
from maccluster.services.sync_service import exit_code_for_sync, sync_home


def _render_plain(result) -> str:
    lines: list[str] = [
        f"sync home  strategy={result.strategy}  dry_run={result.dry_run}  "
        f"compare={result.compare_only}  policy={result.conflict_policy}",
        f"local={result.local_home}",
    ]
    if result.includes:
        lines.append(f"includes={', '.join(result.includes)}")
    if result.safetynet:
        lines.append("safetynet=on")
    if result.verify:
        lines.append("verify=on")
    if result.quick:
        lines.append("quick=on")
    if result.log_path:
        lines.append(f"log={result.log_path}")
    if result.apfs_snapshot:
        lines.append(f"apfs_snapshot={result.apfs_snapshot}")
    for p in result.peers:
        status = "OK" if p.ok else "FAIL"
        lines.append(
            f"  [{status}] {p.peer_id} ({p.peer_ip}) via {p.ssh_target}  "
            f"push={p.push_files}/{p.push_bytes}B pull={p.pull_files}/{p.pull_bytes}B  "
            f"rc={p.push_rc}/{p.pull_rc}  {p.message}"
        )
        if result.compare_only or result.dry_run:
            lines.append(
                f"    stats: only_local={p.only_local} only_remote={p.only_remote} "
                f"local_newer={p.local_newer} remote_newer={p.remote_newer} "
                f"equal={p.equal} skip_conflict={p.conflicts_skipped}"
            )
        if p.sample_push and (result.compare_only or result.dry_run):
            for row in p.sample_push[:12]:
                lines.append(f"    push + {row}")
        if p.sample_pull and (result.compare_only or result.dry_run):
            for row in p.sample_pull[:12]:
                lines.append(f"    pull + {row}")
        if p.push_stdout.strip() and not result.compare_only:
            for row in p.push_stdout.strip().splitlines()[:40]:
                lines.append(f"    push: {row}")
        if p.pull_stdout.strip() and not result.compare_only:
            for row in p.pull_stdout.strip().splitlines()[:40]:
                lines.append(f"    pull: {row}")
        if not p.ok and (p.push_stderr or p.pull_stderr):
            err = (p.push_stderr or p.pull_stderr).strip().splitlines()
            for row in err[:8]:
                lines.append(f"    err: {row}")
        if p.safetynet_backed_up:
            lines.append(f"    safetynet: {p.safetynet_backed_up} files")
        if p.verify_ok is not None:
            lines.append(
                f"    verify: {'OK' if p.verify_ok else 'FAIL'} "
                f"checked={p.verify_checked} mismatches={p.verify_mismatches}"
            )
        if p.truncated:
            lines.append("    note: batch limit hit — re-run for remainder")
    if result.dry_run:
        lines.append(
            "(dry-run / compare — no files written)"
            if result.compare_only
            else "(dry-run — no files written)"
        )
    return "\n".join(lines)


def run(ctx: AppContext, args) -> int:
    action = getattr(args, "sync_action", None)
    if action != "home":
        raise CliError(
            "sync requires a target: maccluster sync home  (see --help)",
            exit_code=2,
        )

    if bool(getattr(args, "last", False)):
        data = read_last_run()
        if ctx.json_mode:
            print(dumps("sync", data or {"error": "no runs logged"}))
        else:
            print(format_last_run(data))
        return 0 if data else 1

    extra = tuple(getattr(args, "exclude", None) or ())
    presets = tuple(getattr(args, "preset", None) or ())
    includes = tuple(getattr(args, "include", None) or ())
    timeout = float(getattr(args, "timeout", None) or TIMEOUT_SYNC)
    if timeout < 30:
        raise CliError("--timeout must be >= 30 seconds", exit_code=2)

    max_files = getattr(args, "max_files", None)
    max_bytes = getattr(args, "max_bytes", None)
    if max_files is not None and max_files < 1:
        raise CliError("--max-files must be >= 1", exit_code=2)
    if max_bytes is not None and max_bytes < 1:
        raise CliError("--max-bytes must be >= 1", exit_code=2)

    no_progress = bool(getattr(args, "no_progress", False)) or ctx.json_mode
    if no_progress:
        progress = NullProgress()
    else:
        progress = SyncProgress(enabled=True, stream=sys.stderr, force=False)

    result = sync_home(
        ctx,
        dry_run=bool(getattr(args, "dry_run", False)),
        compare_only=bool(getattr(args, "compare", False)),
        peer=getattr(args, "peer", None),
        push_only=bool(getattr(args, "push_only", False)),
        pull_only=bool(getattr(args, "pull_only", False)),
        user=getattr(args, "user", None),
        home=getattr(args, "home", None),
        remote_home=getattr(args, "remote_home", None),
        extra_excludes=extra,
        exclude_from=getattr(args, "exclude_from", None),
        presets=presets,
        includes=includes,
        conflict_policy=getattr(args, "conflict_policy", None) or "newer",
        safetynet=bool(getattr(args, "safetynet", False)),
        verify=bool(getattr(args, "verify", False)),
        verify_sample=int(getattr(args, "verify_sample", 20) or 20),
        quick=bool(getattr(args, "quick", False)),
        max_files=max_files,
        max_bytes=max_bytes,
        apfs_snapshot=bool(getattr(args, "apfs_snapshot", False)),
        notify=bool(getattr(args, "notify", False)),
        no_speedtest=bool(getattr(args, "no_speedtest", False)),
        min_free_bytes=getattr(args, "min_free", None),
        timeout=timeout,
        progress=progress,
        force_icloud=bool(getattr(args, "force_icloud", False)),
        identical=bool(getattr(args, "identical", False)),
        icloud_timeout_per_file=float(getattr(args, "icloud_timeout", 20.0) or 20.0),
        icloud_max_seconds=float(getattr(args, "icloud_max_seconds", 900.0) or 900.0),
    )
    code = exit_code_for_sync(result)
    if ctx.json_mode:
        print(dumps("sync", to_jsonable(result)))
    else:
        print(_render_plain(result))
    return code
