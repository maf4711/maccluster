"""docsync command line."""

from __future__ import annotations

import argparse
import fcntl
import os
import shutil
import socket
import sys
import time
from pathlib import Path

from docsync import __version__, agent, discover, speedtest, tbsketch, thunderbolt
from docsync.config import (CONFIG_FILE, MACCLUSTER_CONFIG, STATE_DIR, Config, Peer, load_config,
                            local_hostnames, peers_from_maccluster, save_config)
from docsync.manifest import scan
from docsync.report import RunReport, fmt_bytes
from docsync.session import base_file, run_many, run_peer
from docsync.transport import LocalTransport, RemoteTransport, rsync_bin


def _transport(peer: Peer, host: str):
    if host.startswith("/"):
        return LocalTransport(Path(host))
    return RemoteTransport(peer.userhost(host), peer.path)


def _resolve(peer: Peer, log) -> tuple[str | None, str]:
    """Pick the first reachable host: Thunderbolt first (like maccluster), then LAN, then Bonjour,
    then unconfigured Thunderbolt neighbours from the ARP table. Returns (host, transport)."""
    for h, via in discover.order_hosts(peer.hosts):
        if via.endswith("(link down)"):
            log(f"   {h:<28} skipped — {via}")
            continue
        ms = discover.ping(h)
        log(f"   {h:<28} {'%.1f ms  via %s' % (ms, via) if ms is not None else 'unreachable'}")
        if ms is not None:
            return h, via
    host = discover.bonjour_fallback(peer.hosts)
    if host:
        log(f"   {host:<28} found via Bonjour (name suffix changed)")
        return host, "LAN (Bonjour)"
    for ip in discover.tb_neighbours():
        if ip in peer.hosts:
            continue
        try:
            name = RemoteTransport(peer.userhost(ip), peer.path).ssh("hostname -s", timeout=10).stdout.strip()
        except Exception:  # noqa: BLE001
            continue
        if name and discover.base_name(name) in {discover.base_name(h) for h in peer.hosts}:
            log(f"   {ip:<28} Thunderbolt neighbour answering as {name}")
            return ip, "Thunderbolt (ARP)"
    return None, ""


def resolve_peer(peer: Peer) -> tuple[str | None, str]:
    return _resolve(peer, lambda *a: None)


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
    print(thunderbolt.render(thunderbolt.state()))
    for p in cfg.peers:
        host, via = resolve_peer(p)
        state = f"→ {host} via {via}" if host else "unreachable"
        b = base_file(p.name)
        last = time.strftime("%Y-%m-%d %H:%M", time.localtime(b.stat().st_mtime)) if b.exists() else "never"
        print(f"   {p.name:<10} user={p.user or os.environ.get('USER', '?'):<8} {state:<48} last sync: {last}")
    print(agent.status())
    return 0


def cmd_tb(a) -> int:
    st = thunderbolt.state()
    me = socket.gethostname().split(".")[0]
    model = tbsketch.local_model()
    print(thunderbolt.render(st))
    print()
    print("── cabling sketch (what sits in which receptacle)")
    print(tbsketch.sketch_mac(me, model, st))
    cfg = None
    try:
        cfg = load_config()
    except FileNotFoundError:
        pass
    peers_seen: list[tuple[str, str, thunderbolt.TBState]] = []
    if cfg and not a.no_remote:
        for p in cfg.peers:
            host, via = resolve_peer(p)
            if not host:
                print(f"   {p.name}  —  unreachable, no remote view")
                continue
            try:
                out = RemoteTransport(p.userhost(host), p.path).ssh(tbsketch.REMOTE_CMD, timeout=40).stdout
                pmodel, pst = tbsketch.parse_remote(out)
            except Exception as e:  # noqa: BLE001
                print(f"   {p.name}  —  remote view failed: {e}")
                continue
            peers_seen.append((p.name, pmodel, pst))
            print(tbsketch.sketch_mac(f"{p.name} ({host}, via {via})", pmodel, pst, highlight_free=not st.link_up))
    if cfg:
        print()
        for p in cfg.peers:
            tb_hosts = [h for h, via in discover.order_hosts(p.hosts) if via.startswith("Thunderbolt")]
            if not tb_hosts:
                print(f"   {p.name:<10} no Thunderbolt address configured")
                continue
            ms = discover.ping(tb_hosts[0]) if st.link_up else None
            print(f"   {p.name:<10} {tb_hosts[0]:<16} {'%.1f ms over Thunderbolt' % ms if ms is not None else 'not reachable over Thunderbolt'}")
    if not st.link_up:
        print()
        print("── no Thunderbolt link on this Mac — how to plug it")
        peer_name, peer_model = (peers_seen[0][0], peers_seen[0][1]) if peers_seen else ("peer", "")
        if cfg and not peers_seen:
            peer_name = cfg.peers[0].name if cfg.peers else "peer"
        print(tbsketch.sketch_wrong_right(me, peer_name, peer_model))
    return 0 if st.link_up else 1


def cmd_speedtest(a) -> int:
    cfg = load_config()
    peers = _select_peers(cfg, a.peers, a.all)
    results: list[speedtest.Result] = []
    tb_speed = thunderbolt.state().best_speed
    for p in peers:
        if a.all_hosts:
            targets = [(h, via) for h, via in discover.order_hosts(p.hosts) if discover.ping(h) is not None]
            if not targets:
                print(f"── {p.name}: no host answers a ping")
                continue
        else:
            host, via = _resolve(p, lambda *x: None)
            if not host:
                print(f"── {p.name}: unreachable")
                continue
            targets = [(host, via)]
        for host, via in targets:
            if via == "Thunderbolt" and tb_speed:
                via = f"Thunderbolt {tb_speed} Gb/s"
            pings = [m for m in (discover.ping(host) for _ in range(3)) if m is not None]
            ping_ms = sum(pings) / len(pings) if pings else None
            print(f"── {p.name}  {host} via {via}  … testing {a.seconds:.0f}s per direction", flush=True)
            t = RemoteTransport(p.userhost(host), p.path)
            r = speedtest.run(p.name, host, via, p.userhost(host), t.ssh_cmd, a.seconds, ping_ms,
                              skip_tcp=a.ssh_only, skip_ssh=a.tcp_only)
            results.append(r)
            print(r.render())
    print()
    print(speedtest.summary(results))
    return 0 if results and not any(r.errors for r in results) else 1


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
    tb_speed = thunderbolt.state().best_speed if peers else 0
    try:
        jobs = []
        vias: dict[str, str] = {}
        for p in peers:  # resolve hosts first (quick, sequential, readable output)
            log(f"── {p.name}")
            host, via = _resolve(p, log)
            if host is None:
                jobs.append((p.name, _Unreachable(p)))
                continue
            if via == "Thunderbolt" and tb_speed:
                via = f"Thunderbolt {tb_speed} Gb/s"
            vias[p.name] = via
            jobs.append((p.name, _transport(p, host)))
        parallel = not a.sequential and len(jobs) > 1
        if parallel:
            log(f"syncing {len(jobs)} peers in parallel")
        reps = run_many(cfg, jobs, parallel=parallel, max_workers=a.jobs, log=log, dry=dry, verbose=a.verbose,
                        progress=not a.quiet and sys.stderr.isatty(), force_deletes=a.force_deletes)
        for rep in reps:
            rep.via = vias.get(rep.peer, "")
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
    print(thunderbolt.render(thunderbolt.state()))
    for p in cfg.peers:
        host, via = resolve_peer(p)
        if not host:
            print(f"peer {p.name:<6} unreachable (ping + Bonjour + TB neighbours)")
            ok = False
            continue
        try:
            print(f"peer {p.name:<6} {host} via {via} → ssh ok: {_transport(p, host).check()}")
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
    s = sub.add_parser("tb", help="Thunderbolt ports, link, speed, cabling sketch of this Mac and every peer")
    s.add_argument("--no-remote", action="store_true", help="skip ssh to the peers")
    s.set_defaults(fn=cmd_tb)
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
        s.add_argument("--force-deletes", action="store_true", help="override the max_delete guard")
        s.add_argument("--sequential", action="store_true", help="one peer after another (default: all peers in parallel)")
        s.add_argument("-j", "--jobs", type=int, default=0, help="max parallel peers (default: all)")
        s.set_defaults(fn=lambda a, d=dry: cmd_sync(a, dry=d))

    s = sub.add_parser("watch", help="sync all peers every N minutes (foreground)")
    s.add_argument("--interval", type=int, default=10, metavar="MIN")
    s.set_defaults(fn=cmd_watch, peers=[], all=True, verbose=False, quiet=False, force_deletes=False, sequential=False, jobs=0)

    s = sub.add_parser("speedtest", help="throughput to each peer: raw TCP and the ssh path rsync uses")
    s.add_argument("peers", nargs="*", help="peer names (default: all)")
    s.add_argument("--all", action="store_true")
    s.add_argument("--all-hosts", action="store_true", help="test every reachable host of a peer (Thunderbolt vs LAN)")
    s.add_argument("--seconds", type=float, default=5.0, help="seconds per direction (default 5)")
    s.add_argument("--tcp-only", action="store_true")
    s.add_argument("--ssh-only", action="store_true")
    s.set_defaults(fn=cmd_speedtest)
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
