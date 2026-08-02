"""sync command — home directory newest-wins over TB/SSH."""

from __future__ import annotations

import sys

from maccluster.app_factory import AppContext
from maccluster.constants import TIMEOUT_SYNC
from maccluster.errors import CliError
from maccluster.render.json_out import dumps, to_jsonable
from maccluster.render.progress import NullProgress, SyncProgress
from maccluster.services.sync_service import exit_code_for_sync, sync_home


def _render_plain(result) -> str:
    lines: list[str] = [
        f"sync home  strategy={result.strategy}  dry_run={result.dry_run}",
        f"local={result.local_home}",
    ]
    for p in result.peers:
        status = "OK" if p.ok else "FAIL"
        lines.append(
            f"  [{status}] {p.peer_id} ({p.peer_ip}) via {p.ssh_target}  "
            f"push_rc={p.push_rc} pull_rc={p.pull_rc}  {p.message}"
        )
        if p.push_stdout.strip():
            for row in p.push_stdout.strip().splitlines()[:40]:
                lines.append(f"    push: {row}")
        if p.pull_stdout.strip():
            for row in p.pull_stdout.strip().splitlines()[:40]:
                lines.append(f"    pull: {row}")
        if not p.ok and (p.push_stderr or p.pull_stderr):
            err = (p.push_stderr or p.pull_stderr).strip().splitlines()
            for row in err[:8]:
                lines.append(f"    err: {row}")
    if result.dry_run:
        lines.append("(dry-run — no files written)")
    return "\n".join(lines)


def run(ctx: AppContext, args) -> int:
    action = getattr(args, "sync_action", None)
    if action != "home":
        raise CliError(
            "sync requires a target: maccluster sync home  (see --help)",
            exit_code=2,
        )

    extra = tuple(getattr(args, "exclude", None) or ())
    timeout = float(getattr(args, "timeout", None) or TIMEOUT_SYNC)
    if timeout < 30:
        raise CliError("--timeout must be >= 30 seconds", exit_code=2)

    no_progress = bool(getattr(args, "no_progress", False)) or ctx.json_mode
    if no_progress:
        progress = NullProgress()
    else:
        progress = SyncProgress(enabled=True, stream=sys.stderr, force=False)

    result = sync_home(
        ctx,
        dry_run=bool(getattr(args, "dry_run", False)),
        peer=getattr(args, "peer", None),
        push_only=bool(getattr(args, "push_only", False)),
        pull_only=bool(getattr(args, "pull_only", False)),
        user=getattr(args, "user", None),
        home=getattr(args, "home", None),
        remote_home=getattr(args, "remote_home", None),
        extra_excludes=extra,
        timeout=timeout,
        progress=progress,
    )
    code = exit_code_for_sync(result)
    if ctx.json_mode:
        print(dumps("sync", to_jsonable(result)))
    else:
        print(_render_plain(result))
    return code
