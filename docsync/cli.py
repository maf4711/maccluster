"""docsync command line."""

from __future__ import annotations

import argparse
import fcntl
import os
import shutil
import sys
import time
from pathlib import Path

from docsync import __version__, agent, discover
from docsync.config import (CONFIG_FILE, MACCLUSTER_CONFIG, STATE_DIR, Config, Peer, load_config,
                            local_hostnames, peers_from_maccluster, save_config)
from docsync.manifest import scan
from docsync.report import RunReport, fmt_bytes
from docsync.session import base_file, run_peer
from docsync.transport import LocalTransport, RemoteTransport, rsync_bin


def _transport(peer: Peer, host: str):
    if host.startswith("/"):
        return LocalTransport(Path(host))
    return RemoteTransport(peer.userhost(host), peer.path)


def _resolve(peer: Peer, log) -> str | None:
    host, probes = discover.first_reachable(peer.hosts)
    for h, ms in probes.items():
        log(f"   {h:<28} {'%.1f ms' % ms if ms is not None else 'unreachable'}")
    if host is None:
        host = discover.bonjour_fallback(peer.hosts)
        if host:
            log(f"   {host:<28} found via Bonjour (name suffix changed)")
    return host


def _lock(state_dir: Path):
    state_dir.mkdir(parents=True, exist_ok=True)
    fh = open(state_dir / "run.lock", "w")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("docsync: another run is in progress — exiting", file=sys.stderr)
        sys.exit(3)
    return fh


def _select_peers(cfg: Config, names: list[str], all_: bool) -> list[Peer]:
    if all_ or not names:
        if not cfg.peers:
            sys.exit("no peers configured — run: docsync init --from-maccluster  or edit " + str(CONFIG_FILE))
        return list(cfg.peers)
    return [cfg.peer(n) for n in names]


# ── commands ────────────────────────────────────────────────────────────────

def cmd_init(a) -> int:
    if CONFIG_FILE.exists() and not a.force:
        print(f"config exists: {CONFIG_FILE} (use --force to overwrite)")
        return 1
    cfg = Config(path=a.path)
    if a.from_maccluster:
        if not MACCLUSTER_CONFIG.exists():
            sys.exit(f"no maccluster config at {MACCLUSTER_CONFIG}")
        cfg.peers = peers_from_maccluster(MACCLUSTER_CONFIG.read_text(), local_hostnames(), user=a.user)
    for spec in a.peer or []:
        name, _, hosts = spec.partition("=")
        cfg.peers.append(Peer(name=name, hosts=hosts.split(",") if hosts else [name + ".local"], user=a.user))
    save_config(cfg)
    print(f"wrote {CONFIG_FILE}")
    for p in cfg.peers:
        print(f"   peer {p.name:<10} {p.userhost(p.hosts[0])}:{p.path}  (+{len(p.hosts) - 1} fallback hosts)")
    if not cfg.peers:
        print("   no peers yet — add [[peers]] blocks or re-run with --peer NAME=host1,host2")
    return 0


def cmd_peers(a) -> int:
    cfg = load_config()
    print(f"local: {cfg.local_root}   excludes: {len(cfg.excludes)}   policy: {cfg.conflict_policy}")
    for p in cfg.peers:
        host, probes = discover.first_reachable(p.hosts)
        host = host or discover.bonjour_fallback(p.hosts)
        state = f"→ {host}" if host else "unreachable"
        b = base_file(p.name)
        last = time.strftime("%Y-%m-%d %H:%M", time.localtime(b.stat().st_mtime)) if b.exists() else "never"
        print(f"   {p.name:<10} user={p.user or os.environ.get('USER', '?'):<8} {state:<30} last sync: {last}")
        for h, ms in probes.items():
            print(f"      {h:<28} {'%.1f ms' % ms if ms is not None else '-'}")
    print(agent.status())
    return 0


def cmd_discover(a) -> int:
    names = discover.bonjour_ssh_hosts(a.seconds)
    if not names:
        print("no _ssh._tcp services found (is Remote Login enabled on the other Macs?)")
        return 1
    mine = local_hostnames()
    for n in names:
        host = discover.to_local_hostname(n)
        tag = "(this Mac)" if host.replace(".local", "").lower() in mine else ""
        ms = discover.ping(host)
        print(f"   {n:<32} {host:<30} {'%.1f ms' % ms if ms is not None else 'no ping'} {tag}")
    print("add one with:  docsync init --force --peer NAME=host.local  (or edit the config)")
    return 0


def cmd_status(a) -> int:
    cfg = load_config()
    m = scan(cfg.local_root, cfg.excludes)
    print(f"{cfg.local_root}: {len(m):,} files, {fmt_bytes(sum(v[1] for v in m.values()))}")
    return cmd_peers(a)


def cmd_sync(a, dry: bool) -> int:
    cfg = load_config()
    peers = _select_peers(cfg, a.peers, a.all)
    log = (lambda *x: None) if a.quiet else print
    lock = _lock(STATE_DIR)
    run = RunReport()
    try:
        for p in peers:
            log(f"── {p.name}")
            host = _resolve(p, log)
            if host is None:
                rep = run_peer(cfg, p.name, _Unreachable(p), dry=dry)
            else:
                rep = run_peer(cfg, p.name, _transport(p, host), dry=dry, verbose=a.verbose, log=log)
            run.peers.append(rep)
    finally:
        lock.close()
    print()
    print(run.render())
    _append_log(run)
    return 0 if run.ok else 2


class _Unreachable:
    def __init__(self, peer: Peer):
        self.peer = peer

    def check(self) -> str:
        from docsync.transport import TransportError
        raise TransportError(f"none of {', '.join(self.peer.hosts)} answered a ping")


def _append_log(run: RunReport) -> None:
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        with open(STATE_DIR / "history.log", "a") as fh:
            fh.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')}\n{run.render()}\n")
    except OSError:
        pass


def cmd_conflicts(a) -> int:
    cfg = load_config()
    hits = sorted(p for p in scan(cfg.local_root, []) if ".conflict-" in p)
    if not hits:
        print("no conflict copies")
        return 0
    for h in hits:
        print(f"   {h}")
    print(f"{len(hits)} conflict copies — review, then delete the loser; the deletion propagates on the next sync")
    return 0


def cmd_history(a) -> int:
    f = STATE_DIR / "history.log"
    if not f.exists():
        print("no history yet")
        return 0
    lines = f.read_text().splitlines()
    print("\n".join(lines[-a.lines:]))
    return 0


def cmd_doctor(a) -> int:
    ok = True
    rs = rsync_bin()
    print(f"rsync      {rs}")
    print(f"ssh        {shutil.which('ssh') or 'MISSING'}")
    print(f"dns-sd     {shutil.which('dns-sd') or 'missing (discover disabled)'}")
    keys = list(Path("~/.ssh").expanduser().glob("id_*.pub"))
    print(f"ssh keys   {', '.join(k.name for k in keys) or 'NONE — run ssh-keygen -t ed25519 and ssh-copy-id each peer'}")
    print(f"config     {CONFIG_FILE} {'ok' if CONFIG_FILE.exists() else 'MISSING (docsync init)'}")
    print(f"state      {STATE_DIR}")
    try:
        cfg = load_config()
    except FileNotFoundError:
        return 1
    print(f"documents  {cfg.local_root} {'ok' if cfg.local_root.is_dir() else 'MISSING'}")
    cloud: set[str] = set()
    n = len(scan(cfg.local_root, cfg.excludes, cloud))
    print(f"           {n:,} local files, {len(cloud):,} cloud-only (iCloud-evicted)")
    if icloud_managed(cfg.local_root):
        print("           ⚠ this folder is iCloud 'Desktop & Documents' — Macs on the same Apple ID are already")
        print("             synced by iCloud; docsync skips evicted files and is meant for peers on OTHER accounts")
    for p in cfg.peers:
        host, _ = discover.first_reachable(p.hosts)
        host = host or discover.bonjour_fallback(p.hosts)
        if not host:
            print(f"peer {p.name:<6} unreachable (ping + Bonjour)")
            ok = False
            continue
        try:
            print(f"peer {p.name:<6} {host} → ssh ok: {_transport(p, host).check()}")
        except Exception as e:  # noqa: BLE001
            print(f"peer {p.name:<6} {host} → ssh FAILED: {e}")
            print(f"            fix: ssh-copy-id {p.userhost(host)}   (and enable Remote Login there)")
            ok = False
    print(agent.status())
    return 0 if ok else 1


def cmd_watch(a) -> int:
    while True:
        try:
            cmd_sync(a, dry=False)
        except SystemExit:
            pass
        time.sleep(a.interval * 60)


def icloud_managed(root: Path) -> bool:
    link = Path("~/Library/Mobile Documents/com~apple~CloudDocs/Documents").expanduser()
    try:
        return link.is_symlink() and link.resolve() == root.resolve()
    except OSError:
        return False


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="docsync", description="Sync ~/Documents between Macs over the LAN (rsync+ssh, bidirectional).")
    ap.add_argument("--version", action="version", version=f"docsync {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init", help="write the config (optionally import peers from maccluster)")
    s.add_argument("--from-maccluster", action="store_true", help=f"import nodes from {MACCLUSTER_CONFIG}")
    s.add_argument("--peer", action="append", metavar="NAME=host1,host2", help="add a peer (repeatable)")
    s.add_argument("--user", default="", help="ssh user on the peers (default: same as here)")
    s.add_argument("--path", default="~/Documents")
    s.add_argument("--force", action="store_true")
    s.set_defaults(fn=cmd_init)

    sub.add_parser("peers", help="peers, reachability, last sync").set_defaults(fn=cmd_peers)
    sub.add_parser("status", help="local tree size + peers").set_defaults(fn=cmd_status)
    s = sub.add_parser("discover", help="Bonjour scan for Macs with Remote Login")
    s.add_argument("--seconds", type=float, default=3.0)
    s.set_defaults(fn=cmd_discover)

    for name, dry, help_ in (("sync", False, "sync with peers (bidirectional) and print the report"),
                             ("plan", True, "dry run: show what sync would do")):
        s = sub.add_parser(name, help=help_)
        s.add_argument("peers", nargs="*", help="peer names (default: all)")
        s.add_argument("--all", action="store_true")
        s.add_argument("-v", "--verbose", action="store_true", help="list every file")
        s.add_argument("-q", "--quiet", action="store_true", help="only the final report")
        s.set_defaults(fn=lambda a, d=dry: cmd_sync(a, dry=d))

    s = sub.add_parser("watch", help="sync all peers every N minutes (foreground)")
    s.add_argument("--interval", type=int, default=10, metavar="MIN")
    s.set_defaults(fn=cmd_watch, peers=[], all=True, verbose=False, quiet=False)

    sub.add_parser("conflicts", help="list *.conflict-* copies").set_defaults(fn=cmd_conflicts)
    s = sub.add_parser("history", help="past reports")
    s.add_argument("-n", "--lines", type=int, default=40)
    s.set_defaults(fn=cmd_history)
    sub.add_parser("doctor", help="check rsync, ssh keys, peers").set_defaults(fn=cmd_doctor)

    s = sub.add_parser("install-agent", help="launchd: sync --all every N minutes")
    s.add_argument("--interval", type=int, default=15, metavar="MIN")
    s.set_defaults(fn=lambda a: print(agent.install(a.interval)) or 0)
    sub.add_parser("uninstall-agent").set_defaults(fn=lambda a: print(agent.uninstall()) or 0)
    return ap


def main(argv: list[str] | None = None) -> int:
    a = build_parser().parse_args(argv)
    try:
        return int(a.fn(a) or 0)
    except FileNotFoundError as e:
        print(f"docsync: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
