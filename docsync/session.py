"""One sync run against one peer: list → plan → resolve conflicts → apply → save base."""

from __future__ import annotations

import os
import socket
import time
from datetime import datetime
from pathlib import Path

from docsync import engine, manifest
from docsync.config import STATE_DIR, Config
from docsync.report import PeerReport
from docsync.transport import TransportError, remove_local


def base_file(peer_name: str, state_dir: Path = STATE_DIR) -> Path:
    return state_dir / f"base-{peer_name}.json"


def _bytes_of(m: manifest.Manifest, paths) -> int:
    return sum(m[p][1] for p in paths if p in m)


def _stamp() -> str:
    return datetime.now().strftime("%Y%m%dT%H%M%S")


def run_peer(cfg: Config, peer_name: str, transport, *, dry: bool = False, verbose: bool = False,
             state_dir: Path = STATE_DIR, log=print) -> PeerReport:
    rep = PeerReport(peer=peer_name, dry=dry)
    local_root = cfg.local_root
    local_root.mkdir(parents=True, exist_ok=True)
    try:
        t0 = time.monotonic()
        rep.host = transport.check()
        rep.phase("connect", t0)

        t0 = time.monotonic()
        local = manifest.scan(local_root, cfg.excludes)
        remote = transport.manifest(cfg.excludes)
        rep.phase("list", t0)
        rep.local_files, rep.local_bytes = len(local), _bytes_of(local, local)
        rep.remote_files, rep.remote_bytes = len(remote), _bytes_of(remote, remote)

        base = manifest.load(base_file(peer_name, state_dir))
        rep.first_sync = base is None
        plan = engine.plan(local, remote, base, cfg.conflict_policy)
        rep.unchanged = sum(1 for p in local if p in remote and local[p] == remote[p])
        log(f"[{peer_name}] plan: {plan.summary}")
        if verbose or dry:
            _print_plan(plan, log)

        t0 = time.monotonic()
        stamp = _stamp()
        host = socket.gethostname().split(".")[0]
        for path, winner in plan.conflicts:
            copy = engine.conflict_name(path, stamp, host)
            if winner == "remote":  # keep my copy under a conflict name, then pull theirs
                if not dry:
                    (local_root / copy).parent.mkdir(parents=True, exist_ok=True)
                    os.replace(local_root / path, local_root / copy)
                plan.pull.append(path)
                plan.push.append(copy)
            else:
                transport.rename(path, copy, dry)
                plan.push.append(path)
                plan.pull.append(copy)
            rep.conflicts.append((path, winner, copy))
        rep.phase("conflicts", t0)

        t0 = time.monotonic()
        st = transport.push(local_root, sorted(plan.push), dry, verbose)
        rep.pushed_files = len(plan.push) if dry else st["files"]
        rep.pushed_bytes = _bytes_of(local, [p for p in plan.push if p in local]) if dry else st["bytes"]
        rep.wire_sent += st["sent"]
        rep.wire_received += st["received"]
        rep.phase("push", t0)

        t0 = time.monotonic()
        st = transport.pull(local_root, sorted(plan.pull), dry, verbose)
        rep.pulled_files = len(plan.pull) if dry else st["files"]
        rep.pulled_bytes = _bytes_of(remote, [p for p in plan.pull if p in remote]) if dry else st["bytes"]
        rep.wire_sent += st["sent"]
        rep.wire_received += st["received"]
        rep.phase("pull", t0)

        t0 = time.monotonic()
        remove_local(local_root, Path.home(), plan.delete_local, stamp, cfg.trash, dry)
        transport.remove(plan.delete_remote, stamp, cfg.trash, dry)
        rep.deleted_local, rep.deleted_remote = len(plan.delete_local), len(plan.delete_remote)
        rep.phase("delete", t0)

        if not dry:
            after = manifest.scan(local_root, cfg.excludes)
            manifest.save(base_file(peer_name, state_dir), engine.merged_base(after),
                          meta={"peer": peer_name, "host": rep.host, "synced_at": datetime.now().isoformat()})
    except (TransportError, OSError) as e:
        rep.ok, rep.error = False, str(e)
    rep.finish()
    return rep


def _print_plan(plan: engine.Plan, log) -> None:
    for label, items in (("push →", plan.push), ("pull ←", plan.pull),
                         ("delete local", plan.delete_local), ("delete remote", plan.delete_remote)):
        for p in items:
            log(f"   {label:<14} {p}")
    for path, winner in plan.conflicts:
        log(f"   {'conflict':<14} {path}  (winner: {winner})")
