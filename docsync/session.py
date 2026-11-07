"""One sync run against one peer: list → plan → resolve conflicts → apply → save base."""

from __future__ import annotations

import os
import socket
import time
from datetime import datetime
from pathlib import Path

from docsync import engine, manifest
from docsync.config import STATE_DIR, Config
from docsync.progress import Bar
from docsync.report import PeerReport
from docsync.transport import TransportError, remove_local


def base_file(peer_name: str, state_dir: Path = STATE_DIR) -> Path:
    return state_dir / f"base-{peer_name}.json"


def _bytes_of(m: manifest.Manifest, paths) -> int:
    return sum(m[p][1] for p in paths if p in m)


def _stamp() -> str:
    return datetime.now().strftime("%Y%m%dT%H%M%S")


def _fold(cfg: Config):
    return (lambda k: k.casefold()) if cfg.case_insensitive else (lambda k: k)


def run_peer(cfg: Config, peer_name: str, transport, *, dry: bool = False, verbose: bool = False,
             state_dir: Path = STATE_DIR, log=print, progress: bool = False,
             force_deletes: bool = False, progress_stream=None) -> PeerReport:
    rep = PeerReport(peer=peer_name, dry=dry)
    local_root = cfg.local_root
    local_root.mkdir(parents=True, exist_ok=True)
    try:
        t0 = time.monotonic()
        rep.host = transport.check()
        rep.phase("connect", t0)

        t0 = time.monotonic()
        cloud_local: set[str] = set()
        cloud_remote: set[str] = set()
        log(f"[{peer_name}] listing local tree…")
        local = manifest.scan(local_root, cfg.excludes, cloud_local)
        log(f"[{peer_name}] listing remote tree…")
        remote = transport.manifest(cfg.excludes, cloud_remote)
        rep.phase("list", t0)
        rep.local_files, rep.local_bytes = len(local), _bytes_of(local, local)
        rep.remote_files, rep.remote_bytes = len(remote), _bytes_of(remote, remote)
        rep.cloud_only_local, rep.cloud_only_remote = len(cloud_local), len(cloud_remote)

        # Compare on folded keys (APFS is case-insensitive: 'Befunde/x' == 'befunde/x') but keep
        # each side's own spelling for rsync, so no name changes case and nothing is "deleted".
        fold = _fold(cfg)
        local_name = {fold(k): k for k in local}
        remote_name = {fold(k): k for k in remote}
        local = {fold(k): v for k, v in local.items()}
        remote = {fold(k): v for k, v in remote.items()}
        # Cloud-only (iCloud-evicted) files are invisible to this run on BOTH sides and in the
        # base, so they are never downloaded, never "deleted", and their base entry survives.
        hidden = {fold(h) for h in cloud_local | cloud_remote}
        for h in hidden:
            local.pop(h, None)
            remote.pop(h, None)
        old_base_raw = manifest.load(base_file(peer_name, state_dir))
        old_base = None if old_base_raw is None else {fold(k): v for k, v in old_base_raw.items()}
        rep.first_sync = old_base is None
        base = None if old_base is None else {k: v for k, v in old_base.items() if k not in hidden}
        plan = engine.plan(local, remote, base, cfg.conflict_policy)
        # back to real spellings: push/delete-local use ours, pull/delete-remote use theirs
        plan.push = [local_name.get(p, p) for p in plan.push]
        plan.delete_local = [local_name.get(p, p) for p in plan.delete_local]
        plan.pull = [remote_name.get(p, p) for p in plan.pull]
        plan.delete_remote = [remote_name.get(p, p) for p in plan.delete_remote]
        plan.conflicts = [(local_name.get(p, p), w) for p, w in plan.conflicts]
        rep.unchanged = sum(1 for p in local if p in remote and local[p] == remote[p])
        ndel = len(plan.delete_local) + len(plan.delete_remote)
        if ndel > cfg.max_delete and not force_deletes:
            rep.warnings.append(f"deletion guard: {ndel} deletions exceed max_delete={cfg.max_delete} — "
                                f"skipped all deletions (review with `docsync plan -v`, then `sync --force-deletes`)")
            rep.deletes_skipped = ndel
            plan.delete_local, plan.delete_remote = [], []
        log(f"[{peer_name}] plan: {plan.summary}")
        if verbose or dry:
            _print_plan(plan, log)

        t0 = time.monotonic()
        stamp = _stamp()
        host = socket.gethostname().split(".")[0]
        for path, winner in plan.conflicts:
            copy = engine.conflict_name(path, stamp, host)
            try:
                if winner == "remote":  # keep my copy under a conflict name, then pull theirs
                    if not dry:
                        (local_root / copy).parent.mkdir(parents=True, exist_ok=True)
                        os.replace(local_root / path, local_root / copy)
                    plan.pull.append(remote_name.get(fold(path), path))
                    plan.push.append(copy)
                else:
                    transport.rename(path, copy, dry)
                    plan.push.append(path)
                    plan.pull.append(copy)
            except (TransportError, OSError) as e:  # file changed under us (another sync in flight)
                rep.warnings.append(f"conflict skipped, file vanished: {path} ({e})")
                continue
            rep.conflicts.append((path, winner, copy))
        rep.phase("conflicts", t0)

        t0 = time.monotonic()
        bar = _bar(progress and not dry, "push →", plan.push, local, progress_stream)
        st = transport.push(local_root, sorted(plan.push), dry, verbose, bar.feed if bar else None)
        if bar:
            bar.finish(st["bytes"], st["files"])
        rep.pushed_files = len(plan.push) if dry else st["files"]
        rep.pushed_bytes = _bytes_of(local, [p for p in plan.push if p in local]) if dry else st["bytes"]
        rep.wire_sent += st["sent"]
        rep.wire_received += st["received"]
        _skipped(rep, st, "push")
        rep.phase("push", t0)

        t0 = time.monotonic()
        bar = _bar(progress and not dry, "pull ←", plan.pull, remote, progress_stream)
        st = transport.pull(local_root, sorted(plan.pull), dry, verbose, bar.feed if bar else None)
        if bar:
            bar.finish(st["bytes"], st["files"])
        rep.pulled_files = len(plan.pull) if dry else st["files"]
        rep.pulled_bytes = _bytes_of(remote, [p for p in plan.pull if p in remote]) if dry else st["bytes"]
        rep.wire_sent += st["sent"]
        rep.wire_received += st["received"]
        _skipped(rep, st, "pull")
        rep.phase("pull", t0)

        t0 = time.monotonic()
        remove_local(local_root, Path.home(), plan.delete_local, stamp, cfg.trash, dry)
        transport.remove(plan.delete_remote, stamp, cfg.trash, dry)
        rep.deleted_local, rep.deleted_remote = len(plan.delete_local), len(plan.delete_remote)
        rep.phase("delete", t0)

        if not dry:
            after = manifest.scan(local_root, cfg.excludes, set())
            new_base = engine.merged_base({k: v for k, v in after.items() if fold(k) not in hidden})
            if old_base_raw:  # keep what we knew about cloud-only files
                for k, v in old_base_raw.items():
                    if fold(k) in hidden:
                        new_base[k] = v
            manifest.save(base_file(peer_name, state_dir), new_base,
                          meta={"peer": peer_name, "host": rep.host, "synced_at": datetime.now().isoformat()})
    except (TransportError, OSError) as e:
        rep.ok, rep.error = False, str(e)
    rep.finish()
    return rep


def _bar(enabled: bool, label: str, paths: list[str], m: manifest.Manifest, stream=None):
    if not enabled or not paths:
        return None
    return Bar(label, len(paths), _bytes_of(m, paths), stream=stream, enabled=True if stream else None)


def run_many(cfg: Config, jobs: list[tuple[str, object]], *, parallel: bool = True, max_workers: int = 0,
             state_dir: Path = STATE_DIR, log=print, progress: bool = False, **kw) -> list[PeerReport]:
    """Sync several peers. jobs = [(peer_name, transport-or-callable-returning-transport)].
    Parallel: one thread per peer, one progress line per peer. Reports come back in job order."""
    from concurrent.futures import ThreadPoolExecutor
    from docsync.progress import MultiDisplay

    names = [n for n, _ in jobs]
    display = MultiDisplay(names) if (parallel and progress and len(jobs) > 1) else None

    def one(name, tr):
        transport = tr() if callable(tr) and not hasattr(tr, "check") else tr
        plog = (lambda *a: log(f"[{name}]", *a)) if display is None else (lambda *a: display.update(name, f"   {name:<10}" + " ".join(str(x) for x in a)))
        return run_peer(cfg, name, transport, state_dir=state_dir, log=plog, progress=progress,
                        progress_stream=display.slot(name) if display else None, **kw)

    if not parallel or len(jobs) == 1:
        return [one(n, t) for n, t in jobs]
    with ThreadPoolExecutor(max_workers=max_workers or len(jobs)) as ex:
        futs = [ex.submit(one, n, t) for n, t in jobs]
        reps = [f.result() for f in futs]
    if display:
        display.close()
    return reps


def _skipped(rep: PeerReport, st: dict, phase: str) -> None:
    if st.get("skipped"):
        rep.skipped += st["skipped"]
        for e in st.get("errors", []):
            rep.warnings.append(f"{phase}: {e.strip()}")


def _print_plan(plan: engine.Plan, log) -> None:
    for label, items in (("push →", plan.push), ("pull ←", plan.pull),
                         ("delete local", plan.delete_local), ("delete remote", plan.delete_remote)):
        for p in items:
            log(f"   {label:<14} {p}")
    for path, winner in plan.conflicts:
        log(f"   {'conflict':<14} {path}  (winner: {winner})")
