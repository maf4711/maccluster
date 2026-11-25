"""Stable engine API for programs embedding docsync (maccluster): explicit roots, peers, hosts,
users and bind addresses in; a structured result out. No argparse, no printing, never exits the process.

    from docsync import api
    res = api.sync(api.SyncRequest(
        roots=[api.RootSpec("documents", "~/Documents")],
        peers=[api.PeerSpec("node-b", "10.42.0.2", user="a321", bind_ip="10.42.0.3", via="Thunderbolt")]))
    res.ok, res.exit_code, res.to_dict()
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from docsync import icloud, lock, watchdog
from docsync.config import DEFAULT_EXCLUDES, STATE_DIR, Config, Peer, Root
from docsync.report import RunReport

API_VERSION = 1

ProgressCB = Callable[[str, str, str, int, int], None]  # (peer, root, phase, done_bytes, total_bytes)


@dataclass
class RootSpec:
    name: str
    local_path: str
    peer_path: str = ""              # default: same as local_path
    excludes: list[str] | None = None  # default: SyncRequest.excludes


@dataclass
class _ConfigRootSpec(RootSpec):
    """docsync's own CLI only: a config root with an `includes` allowlist. Not part of the stable
    API (API_VERSION stays 1); RootSpec itself has no includes."""
    includes: list[str] | None = None


@dataclass
class PeerSpec:
    name: str
    host: str | None                 # None → reported as unreachable; "/abs/dir" → local directory peer
    user: str = ""
    bind_ip: str | None = None       # ssh -b, e.g. the Thunderbolt bridge address
    via: str = ""                    # label for the report ("Thunderbolt 80 Gb/s", "LAN")
    paths: dict[str, str] = field(default_factory=dict)  # per-root peer path override
    hosts_tried: list[str] = field(default_factory=list)  # for the unreachable message


@dataclass
class SyncRequest:
    roots: list[RootSpec]
    peers: list[PeerSpec]
    conflict_policy: str = "newest"
    max_delete: int = 25
    force_deletes: bool = False
    transfer_streams: int = 4
    case_insensitive: bool = True
    trash: bool = True
    excludes: list[str] = field(default_factory=lambda: list(DEFAULT_EXCLUDES))
    safetynet: bool = True
    safetynet_keep: int = 5
    safetynet_max_age_days: float = 14.0
    safetynet_max_gb: float = 20.0
    dry_run: bool = False
    parallel: bool = True
    max_workers: int = 0
    verbose: bool = False
    terminal_progress: bool = False   # draw docsync's own progress bars on stderr
    progress: ProgressCB | None = None
    log: Callable[..., None] | None = None
    state_dir: Path | None = None
    lock: bool = True
    lock_wait: float = 0.0
    max_runtime: float = watchdog.DEFAULT_MAX_RUNTIME  # caller's own run limit: the peer lock lasts that + 60 s (<= 0: no cap)


@dataclass
class SyncResult:
    report: RunReport
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error and self.report.ok

    @property
    def exit_code(self) -> int:
        if self.error.startswith("another run") or self.report.all_busy:
            return 3  # every peer busy: another run covers them all
        return 0 if self.ok else 2

    def to_dict(self) -> dict:
        d = self.report.to_dict()
        d.update({"api_version": API_VERSION, "ok": self.ok, "exit_code": self.exit_code, "error": self.error})
        return d


def _config(req: SyncRequest) -> Config:
    return Config(
        path=req.roots[0].local_path, excludes=list(req.excludes), conflict_policy=req.conflict_policy,
        trash=req.trash, case_insensitive=req.case_insensitive, max_delete=req.max_delete,
        transfer_streams=req.transfer_streams, safetynet=req.safetynet, safetynet_keep=req.safetynet_keep,
        safetynet_max_age_days=req.safetynet_max_age_days, safetynet_max_gb=req.safetynet_max_gb,
        roots=[Root(r.name, r.local_path, r.peer_path, r.excludes, includes=getattr(r, "includes", None))
               for r in req.roots],
    )


def sync(req: SyncRequest) -> SyncResult:
    """Run every root against every peer. Never raises for peer/transport problems — they are in
    the result. Raises ValueError for an invalid request before touching the network."""
    from docsync.hosts import Unreachable, transport_for
    from docsync.session import run_roots

    if not req.roots:
        raise ValueError("no roots")
    if not req.peers:
        raise ValueError("no peers")
    if req.conflict_policy not in ("newest", "local", "remote"):
        raise ValueError(f"conflict_policy must be newest|local|remote, got {req.conflict_policy!r}")
    started = time.monotonic()
    cfg = _config(req)
    state_dir = req.state_dir or STATE_DIR
    fh = None
    if req.lock:
        fh = lock.acquire(state_dir, req.lock_wait)
        if fh is None:
            return SyncResult(RunReport(), error=lock.busy_message(state_dir))
    try:
        roots = cfg.effective_roots()
        covered = []  # unreachable peers whose root the last icloud-check passed on both sides
        me = icloud.node_name() if any(p.host is None for p in req.peers) else ""

        def jobs_for_root(root: Root):
            jobs = []
            for p in req.peers:
                peer = Peer(p.name, p.hosts_tried or ([p.host] if p.host else []), p.user)
                if p.host is None:
                    if icloud.covered(state_dir, me, p.name, root.name):
                        covered.append(icloud.covered_report(p.name, root.name))
                    else:
                        jobs.append((p.name, Unreachable(peer)))
                    continue
                path = p.paths.get(root.name) or root.peer_path or root.path
                jobs.append((p.name, transport_for(peer, p.host, path, bind=p.bind_ip)))
            return jobs

        plan = {r.name: jobs_for_root(r) for r in roots}
        active = [r for r in roots if plan[r.name]]
        reps = run_roots(cfg, active, lambda r: plan[r.name], state_dir=state_dir, parallel=req.parallel,
                         max_workers=req.max_workers, log=req.log or (lambda *a: None), dry=req.dry_run,
                         verbose=req.verbose, progress=req.terminal_progress, force_deletes=req.force_deletes,
                         on_progress=req.progress, max_runtime=req.max_runtime) if active else []
    finally:
        if fh is not None:
            fh.close()
    if covered:  # back into root, then peer order
        r_ix, p_ix = {r.name: i for i, r in enumerate(roots)}, {p.name: i for i, p in enumerate(req.peers)}
        reps = sorted(reps + covered, key=lambda rep: (r_ix.get(rep.root, 0), p_ix.get(rep.peer, 0)))
    run = RunReport(started=started)
    vias = {p.name: p.via for p in req.peers}
    for rep in reps:
        rep.via = rep.via or vias.get(rep.peer, "")
        run.peers.append(rep)
    return SyncResult(run)


def resolve_peer(hosts: list[str], user: str = "", name: str = "peer", log=None) -> tuple[str | None, str]:
    """Opt-in auto-detection (Thunderbolt, then TB link-local/ARP, then LAN, then Bonjour)."""
    from docsync.hosts import resolve
    return resolve(Peer(name, list(hosts), user), log or (lambda *a: None))
