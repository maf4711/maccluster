"""One sync run against one peer: list → plan → resolve conflicts → apply → save base."""

from __future__ import annotations

import inspect
import os
import socket
import time
from datetime import datetime
from pathlib import Path

from docsync import engine, lock, manifest, safetynet, xferwatch
from docsync.config import STATE_DIR, Config, Root
from docsync.progress import Bar
from docsync.report import PeerReport
from docsync.transport import TransportError, remove_local


def base_file(peer_name: str, state_dir: Path = STATE_DIR, root: str = "documents") -> Path:
    return state_dir / f"base-{root}-{peer_name}.json"


def _migrate_base(peer_name: str, state_dir: Path, root: str) -> None:
    """0.x kept one base per peer (base-<peer>.json); it belongs to the root "documents"."""
    if root != "documents":
        return
    legacy, new = state_dir / f"base-{peer_name}.json", base_file(peer_name, state_dir, root)
    if legacy.exists() and not new.exists():
        legacy.rename(new)


def _bytes_of(m: manifest.Manifest, paths) -> int:
    return sum(m[p][1] for p in paths if p in m)


def _stamp() -> str:
    return datetime.now().strftime("%Y%m%dT%H%M%S")


def _fold(cfg: Config):
    return (lambda k: k.casefold()) if cfg.case_insensitive else (lambda k: k)


def scan_local(cfg: Config):
    """(manifest, cloud-only set, denied set, symlink set) of the local tree — shared between peers in run_many."""
    cloud: set[str] = set()
    denied: set[str] = set()
    links: set[str] = set()
    m = manifest.scan(cfg.local_root, cfg.excludes, cloud, cfg.includes, denied, cfg.case_insensitive, links)
    return m, cloud, denied, links


def _under(key: str, links: set[str]) -> bool:
    """key is a symlink or lies below one (any path component)."""
    parts = key.split("/")
    return any("/".join(parts[:i]) in links for i in range(1, len(parts) + 1))


def _takes_links(fn) -> bool:
    params = inspect.signature(fn).parameters.values()
    return any(p.name == "links" or p.kind is inspect.Parameter.VAR_KEYWORD for p in params)


def _symlink_skips(keys: set[str], links: set[str]) -> set[str]:
    return {k for k in keys if _under(k, links)} if links else set()


def run_peer(cfg: Config, peer_name: str, transport, *, dry: bool = False, verbose: bool = False,
             state_dir: Path = STATE_DIR, log=print, progress: bool = False,
             force_deletes: bool = False, progress_stream=None, local_source=None,
             root_name: str = "documents", on_progress=None, root_locks: lock.RootLocks | None = None) -> PeerReport:
    """local_source: callable returning scan_local's (manifest, cloud, denied, links); when given, the remote listing is
    requested first and the local scan result fetched afterwards (they overlap in run_many).
    root_locks: the run's root locks (run_many shares one); dry runs take none."""
    rep = PeerReport(peer=peer_name, dry=dry, root=root_name)
    local_root = cfg.local_root
    if not local_root.is_dir():  # never create a root: a missing one is a typo or an unmounted volume
        rep.ok, rep.error = False, f"root missing: {local_root}"
        rep.finish()
        return rep
    bfile = base_file(peer_name, state_dir, root_name)
    locks = None if dry else (root_locks or lock.RootLocks(state_dir, local_root))
    peer_lock = None
    try:
        t0 = time.monotonic()
        rep.host = transport.check()
        if locks is not None:  # after check(): a missing peer folder takes no lock
            peer_lock, busy, warns = locks.pair(transport)
            rep.warnings += warns
            if busy:
                kind, text = busy
                log(f"[{peer_name}] {'peer busy: ' + text if kind == 'peer' else text}")
                raise _Busy(f"peer busy (covered by the other run): {text}" if kind == "peer" else text)
        _migrate_base(peer_name, state_dir, root_name)  # renames a base file: only once the pair is ours
        if hasattr(transport, "prepare"):
            rep.warnings += transport.prepare()
        rep.phase("connect", t0)

        t0 = time.monotonic()
        cloud_remote: set[str] = set()
        log(f"[{peer_name}] listing remote tree…")
        denied_remote: set[str] = set()
        links_remote: set[str] = set()
        # roots without includes keep the 1.2.0 transport call (other transports need not know includes)
        kw = {} if cfg.includes is None else {"includes": cfg.includes, "denied": denied_remote, "icase": cfg.case_insensitive}
        if _takes_links(transport.manifest):
            kw["links"] = links_remote
        else:
            rep.warnings.append(f"peer symlink protection unavailable for {type(transport).__name__}")
            log(f"[{peer_name}] {rep.warnings[-1]}")
        remote = transport.manifest(cfg.excludes, cloud_remote, **kw)
        if local_source is None:
            log(f"[{peer_name}] listing local tree…")
            local_pre, cloud_local, denied_local, links_local = scan_local(cfg)
        else:
            local_pre, cloud_local, denied_local, links_local = local_source()
        local = dict(local_pre)
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
        old_base_raw = manifest.load(bfile)
        old_base = None if old_base_raw is None else {fold(k): v for k, v in old_base_raw.items()}
        # A symlink on either side (and everything below it) is out of this run too: rsync would
        # replace it or write through it. Never a deletion. Unlike cloud-only files its base entries
        # are dropped, so removing the link later pulls the files back instead of deleting them.
        linked: set[str] = set()
        keys = set(local) | set(remote) | set(old_base or {})
        for node, links in ((socket.gethostname().split(".")[0], links_local), (peer_name, links_remote)):
            skip = _symlink_skips(keys - hidden - linked, {fold(manifest.norm(x)) for x in links})
            if skip:
                rep.warnings.append(f"skipped (symlink on {node}): {len(skip)} path(s)")
                log(f"[{peer_name}] skipped (symlink on {node}): {len(skip)} path(s)")
                linked |= skip
        for h in hidden | linked:
            local.pop(h, None)
            remote.pop(h, None)
        rep.first_sync = old_base is None
        base = None if old_base is None else {k: v for k, v in old_base.items() if k not in hidden | linked}
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
        if cfg.includes is not None:
            denied = {fold(p) for p in denied_remote | denied_local}
            log(f"[{peer_name}] denied: {len(denied)} file(s)")
        if verbose or dry:
            _print_plan(plan, log, limit=None if verbose else 20)

        t0 = time.monotonic()
        stamp = _stamp()
        host = socket.gethostname().split(".")[0]
        if plan.conflicts:
            _guard(peer_lock)
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

        if not dry and cfg.safetynet and hasattr(transport, "backup"):
            _guard(peer_lock)
            t0 = time.monotonic()
            try:  # retention on the peer side first, so a full SafetyNet never blocks a backup
                transport.prune_safetynet(cfg.safetynet_policy())
            except Exception as e:  # noqa: BLE001 — retention problems are warnings, not failures
                rep.warnings.append(f"safetynet prune on peer skipped: {e}")
            sn_stamp = safetynet.stamp_now()
            over_local = [p for p in plan.pull if fold(p) in local]
            over_remote = [p for p in plan.push if fold(p) in remote]
            n1, b1 = safetynet.backup_local(local_root, over_local, sn_stamp)  # raises → nothing overwritten
            n2, b2 = transport.backup(over_remote, sn_stamp)
            rep.safetynet_files, rep.safetynet_bytes = n1 + n2, b1 + b2
            rep.phase("safetynet", t0)

        sizes_local = {local_name.get(k, k): v[1] for k, v in local.items()}
        sizes_remote = {remote_name.get(k, k): v[1] for k, v in remote.items()}
        streams = cfg.transfer_streams
        _guard(peer_lock)
        t0 = time.monotonic()
        bar = _bar(progress and not dry, "push →", plan.push, local, progress_stream, multi=streams > 1)
        st = _transfer(peer_lock, transport.push, local_root, sorted(plan.push), dry, verbose,
                            _feeder(bar, on_progress, peer_name, root_name, "push", _bytes_of(local, map(fold, plan.push))),
                            sizes=sizes_local, streams=streams)
        if bar:
            bar.finish(st["bytes"], st["files"])
        rep.pushed_files = len(plan.push) if dry else st["files"]
        rep.pushed_bytes = _bytes_of(local, map(fold, plan.push)) if dry else st["bytes"]  # keys are folded
        rep.wire_sent += st["sent"]
        rep.wire_received += st["received"]
        rep.retries += st.get("retries", 0)
        rep.streams = max(rep.streams, st.get("streams", 1))
        _skipped(rep, st, "push")
        rep.phase("push", t0)

        _guard(peer_lock)
        t0 = time.monotonic()
        bar = _bar(progress and not dry, "pull ←", plan.pull, remote, progress_stream, multi=streams > 1)
        st = _transfer(peer_lock, transport.pull, local_root, sorted(plan.pull), dry, verbose,
                            _feeder(bar, on_progress, peer_name, root_name, "pull", _bytes_of(remote, map(fold, plan.pull))),
                            sizes=sizes_remote, streams=streams)
        rep.streams = max(rep.streams, st.get("streams", 1))
        if bar:
            bar.finish(st["bytes"], st["files"])
        rep.pulled_files = len(plan.pull) if dry else st["files"]
        rep.pulled_bytes = _bytes_of(remote, map(fold, plan.pull)) if dry else st["bytes"]
        rep.wire_sent += st["sent"]
        rep.wire_received += st["received"]
        rep.retries += st.get("retries", 0)
        _skipped(rep, st, "pull")
        rep.phase("pull", t0)

        _guard(peer_lock)
        t0 = time.monotonic()
        remove_local(local_root, Path.home(), plan.delete_local, stamp, cfg.trash, dry)
        transport.remove(plan.delete_remote, stamp, cfg.trash, dry)
        rep.deleted_local, rep.deleted_remote = len(plan.delete_local), len(plan.delete_remote)
        rep.phase("delete", t0)

        if not dry:
            # No second full walk: start from the pre-scan and re-stat only the paths this run
            # touched (pushed, pulled, renamed, deleted). Other peers' concurrent changes stay
            # out of this peer's base on purpose — they must still flow to this peer next time.
            after = _refresh(local_root, local_pre,
                             set(plan.push) | set(plan.pull) | set(plan.delete_local) | {c for _, _, c in rep.conflicts})
            new_base = engine.merged_base({k: v for k, v in after.items() if fold(k) not in hidden | linked})
            if old_base_raw:  # keep what we knew about cloud-only files
                for k, v in old_base_raw.items():
                    if fold(k) in hidden:
                        new_base[k] = v
            _guard(peer_lock)
            manifest.save(bfile, new_base,
                          meta={"peer": peer_name, "host": rep.host, "synced_at": datetime.now().isoformat()})
    except _Busy as e:
        rep.busy, rep.error = True, str(e)
    except (TransportError, OSError) as e:
        rep.ok, rep.error = False, str(e)
    finally:
        if peer_lock is not None:
            peer_lock.release()
        if locks is not None and root_locks is None:
            locks.close()
    rep.finish()
    if on_progress:
        try:
            on_progress(peer_name, root_name, "done", rep.transferred_bytes, rep.transferred_bytes)
        except Exception:  # noqa: BLE001 — a caller's callback must not break the sync
            pass
    return rep


class _Busy(Exception):
    """The root lock of this pair is held by another run."""


LOCK_LOST = "peer lock lost (sleep or link down) — base not written"


def _guard(peer_lock) -> None:
    """Before every write phase: the peer must still hold our lock (a sleep or a dead link frees it)."""
    if peer_lock is not None and not peer_lock.held():
        raise TransportError(LOCK_LOST)


def _transfer(peer_lock, fn, *args, **kw) -> dict:
    """transport.push/pull under an xferwatch.Watch: losing the peer lock mid-transfer ends its rsyncs
    and fails the peer with LOCK_LOST, even when the transfer itself got through."""
    with xferwatch.Watch(peer_lock) as w:
        try:
            st = fn(*args, **kw)
        except TransportError:
            if not w.lost:
                raise
    if w.lost:
        raise TransportError(LOCK_LOST)
    return st


def _feeder(bar, cb, peer: str, root: str, phase: str, total: int):
    """rsync progress-line sink feeding the terminal bar and/or an API callback (summed over streams)."""
    if bar is None and cb is None:
        return None
    from docsync.progress import parse_progress
    done: dict[int, int] = {}

    def feed(line: str, stream: int = 0) -> None:
        if bar is not None:
            bar.feed(line, stream)
        if cb is not None:
            p = parse_progress(line)
            if p:
                done[stream] = p["bytes"]
                try:
                    cb(peer, root, phase, sum(done.values()), total)
                except Exception:  # noqa: BLE001
                    pass
    return feed


def _bar(enabled: bool, label: str, paths: list[str], m: manifest.Manifest, stream=None, multi: bool = False):
    if not enabled or not paths:
        return None
    return Bar(label, len(paths), _bytes_of(m, paths), stream=stream, enabled=True if stream else None, multi=multi)


def _refresh(root: Path, pre: manifest.Manifest, touched: set[str]) -> manifest.Manifest:
    """pre-scan manifest with `touched` paths re-stated (dropped when gone)."""
    out = dict(pre)
    for rel in touched:
        key = manifest.norm(rel)
        try:
            st = os.lstat(root / rel)
            if os.path.isfile(root / rel) and not os.path.islink(root / rel) and not (getattr(st, "st_flags", 0) & manifest.SF_DATALESS):
                out[key] = (int(st.st_mtime), int(st.st_size))
                continue
        except OSError:
            pass
        out.pop(key, None)
    return out


def run_many(cfg: Config, jobs: list[tuple[str, object]], *, parallel: bool = True, max_workers: int = 0,
             state_dir: Path = STATE_DIR, log=print, progress: bool = False,
             max_runtime: float | None = None, **kw) -> list[PeerReport]:
    """Sync several peers. jobs = [(peer_name, transport-or-callable-returning-transport)].
    Parallel: one thread per peer, one progress line per peer. Reports come back in job order."""
    from concurrent.futures import ThreadPoolExecutor
    from docsync.progress import MultiDisplay

    names = [n for n, _ in jobs]
    display = MultiDisplay(names) if (parallel and progress and len(jobs) > 1) else None
    if not kw.get("dry") and cfg.safetynet and cfg.local_root.is_dir():
        try:  # local retention once per root and run (peers prune their side in run_peer)
            safetynet.prune(str(cfg.local_root / safetynet.SN_DIR), *cfg.safetynet_policy())
        except Exception:  # noqa: BLE001
            pass
    locks = None if kw.get("dry") else lock.RootLocks(state_dir, cfg.local_root, max_runtime=max_runtime)
    scan_pool = ThreadPoolExecutor(max_workers=1)
    local_future = scan_pool.submit(scan_local, cfg)  # one local walk, overlapping the remote listings
    local_source = local_future.result

    def one(name, tr):
        transport = tr() if callable(tr) and not hasattr(tr, "check") else tr
        def plog(*a, _name=name):
            msg = " ".join(str(x) for x in a)
            if display is None:
                log(msg if msg.startswith(f"[{_name}]") else f"[{_name}] {msg}")
            else:
                display.update(_name, f"   {_name:<10}" + msg.replace(f"[{_name}] ", ""))
        return run_peer(cfg, name, transport, state_dir=state_dir, log=plog, progress=progress,
                        progress_stream=display.slot(name) if display else None, local_source=local_source,
                        root_locks=locks, **kw)

    try:
        if not parallel or len(jobs) == 1:
            return [one(n, t) for n, t in jobs]
        with ThreadPoolExecutor(max_workers=max_workers or len(jobs)) as ex:
            futs = [ex.submit(one, n, t) for n, t in jobs]
            reps = [f.result() for f in futs]
    finally:
        scan_pool.shutdown(wait=False)
        if locks is not None:
            locks.close()
    if display:
        display.close()
    return reps


def run_roots(cfg: Config, roots: list[Root], jobs_for_root, **kw) -> list[PeerReport]:
    """Every root, one after another; within a root all peers in parallel (run_many).
    jobs_for_root(root) → [(peer_name, transport)] built with that root's peer path."""
    reps: list[PeerReport] = []
    for root in roots:
        reps += run_many(cfg.for_root(root), jobs_for_root(root), root_name=root.name, **kw)
    return reps


def _skipped(rep: PeerReport, st: dict, phase: str) -> None:
    if st.get("skipped"):
        rep.skipped += st["skipped"]
        for e in st.get("errors", []):
            rep.warnings.append(f"{phase}: {e.strip()}")


def _print_plan(plan: engine.Plan, log, limit: int | None = None) -> None:
    """Every planned path with -v; otherwise the first `limit` per category and a count."""
    for label, items in (("push →", plan.push), ("pull ←", plan.pull),
                         ("delete local", plan.delete_local), ("delete remote", plan.delete_remote)):
        shown = items if limit is None else items[:limit]
        for p in shown:
            log(f"   {label:<14} {p}")
        if len(items) > len(shown):
            log(f"   {label:<14} … {len(items) - len(shown):,} more (docsync plan -v lists all)")
    conflicts = plan.conflicts if limit is None else plan.conflicts[:limit]
    for path, winner in conflicts:
        log(f"   {'conflict':<14} {path}  (winner: {winner})")
    if len(plan.conflicts) > len(conflicts):
        log(f"   {'conflict':<14} … {len(plan.conflicts) - len(conflicts):,} more")
