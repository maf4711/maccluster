"""Sync run history (CCC task-log analogue)."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from maccluster.config.paths import default_sync_log_dir, default_sync_state_path
from maccluster.domain.models import SyncHomeResult


def _utc_stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def write_run_log(
    result: SyncHomeResult,
    *,
    log_dir: Path | None = None,
) -> Path:
    """Write one JSON run log under ~/Library/Logs/maccluster/."""
    d = log_dir or default_sync_log_dir()
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"sync-{_utc_stamp()}.json"
    payload = _result_to_dict(result)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    # pointer to last
    last = d / "sync-last.json"
    try:
        if last.exists() or last.is_symlink():
            last.unlink()
        os.symlink(path.name, last)
    except OSError:
        last.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    return path


def read_last_run(*, log_dir: Path | None = None) -> dict[str, Any] | None:
    d = log_dir or default_sync_log_dir()
    last = d / "sync-last.json"
    if last.is_file():
        try:
            return json.loads(last.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
    if not d.is_dir():
        return None
    files = sorted(d.glob("sync-*.json"), reverse=True)
    for f in files:
        if f.name == "sync-last.json":
            continue
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
    return None


def format_last_run(data: dict[str, Any] | None) -> str:
    if not data:
        return "no sync runs logged yet (run: maccluster sync home)"
    lines = [
        f"last sync  strategy={data.get('strategy')}  dry_run={data.get('dry_run')}  "
        f"compare={data.get('compare_only')}  policy={data.get('conflict_policy')}",
        f"local={data.get('local_home')}",
        f"log={data.get('log_path') or '(inline)'}",
    ]
    for p in data.get("peers") or []:
        status = "OK" if p.get("ok") else "FAIL"
        lines.append(
            f"  [{status}] {p.get('peer_id')} ({p.get('peer_ip')})  "
            f"push={p.get('push_files', 0)}/{p.get('push_bytes', 0)}B  "
            f"pull={p.get('pull_files', 0)}/{p.get('pull_bytes', 0)}B  "
            f"{p.get('message', '')}"
        )
    return "\n".join(lines)


def load_sync_state(path: Path | None = None) -> dict[str, Any]:
    p = path or default_sync_state_path()
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def save_sync_state(state: dict[str, Any], path: Path | None = None) -> None:
    p = path or default_sync_state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _result_to_dict(result: SyncHomeResult) -> dict[str, Any]:
    peers = []
    for p in result.peers:
        peers.append(
            {
                "peer_id": p.peer_id,
                "peer_ip": p.peer_ip,
                "ssh_target": p.ssh_target,
                "ok": p.ok,
                "message": p.message,
                "push_rc": p.push_rc,
                "pull_rc": p.pull_rc,
                "push_files": p.push_files,
                "pull_files": p.pull_files,
                "push_bytes": p.push_bytes,
                "pull_bytes": p.pull_bytes,
                "only_local": p.only_local,
                "only_remote": p.only_remote,
                "local_newer": p.local_newer,
                "remote_newer": p.remote_newer,
                "equal": p.equal,
                "conflicts_skipped": p.conflicts_skipped,
                "sample_push": list(p.sample_push),
                "sample_pull": list(p.sample_pull),
                "verify_ok": p.verify_ok,
                "verify_checked": p.verify_checked,
                "verify_mismatches": p.verify_mismatches,
                "safetynet_backed_up": p.safetynet_backed_up,
                "truncated": p.truncated,
            }
        )
    return {
        "ts": datetime.now(UTC).isoformat(),
        "local_home": result.local_home,
        "dry_run": result.dry_run,
        "strategy": result.strategy,
        "conflict_policy": result.conflict_policy,
        "compare_only": result.compare_only,
        "safetynet": result.safetynet,
        "verify": result.verify,
        "quick": result.quick,
        "includes": list(result.includes),
        "excludes": list(result.excludes)[:50],
        "log_path": result.log_path,
        "apfs_snapshot": result.apfs_snapshot,
        "max_files": result.max_files,
        "max_bytes": result.max_bytes,
        "peers": peers,
        "ok": result.ok,
    }
