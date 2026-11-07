"""Diagnostic commands: tb, speedtest, discover, doctor, safetynet."""

from __future__ import annotations

import shutil
import socket
from pathlib import Path

from docsync import agent, discover, safetynet, speedtest, tbsketch, thunderbolt
from docsync.config import CONFIG_FILE, STATE_DIR, load_config, local_hostnames
from docsync.hosts import resolve, resolve_peer, select_peers, transport_for
from docsync.manifest import scan
from docsync.transport import RemoteTransport, rsync_bin


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
    peers = select_peers(cfg, a.peers, a.all)
    results: list[speedtest.Result] = []
    tb_speed = thunderbolt.state().best_speed
    for p in peers:
        if a.all_hosts:
            targets = [(h, via) for h, via in discover.order_hosts(p.hosts) if discover.ping(h) is not None]
            if not targets:
                print(f"── {p.name}: no host answers a ping")
                continue
        else:
            host, via = resolve(p, lambda *x: None)
            if not host:
                print(f"── {p.name}: unreachable")
                continue
            targets = [(host, via)]
        for host, via in targets:
            try:  # a .local name may resolve onto the Thunderbolt bridge (mDNS runs there too)
                ip = socket.gethostbyname(host)
            except OSError:
                ip = host
            if ip != host:
                state = discover.is_tb_host(ip, discover.tb_networks(), discover.cluster_tb_subnet())
                via = "Thunderbolt" if state == "active" else "LAN"
                host = f"{host} ({ip})"
            if via == "Thunderbolt" and tb_speed:
                via = f"Thunderbolt {tb_speed} Gb/s"
            pings = [m for m in (discover.ping(ip) for _ in range(3)) if m is not None]
            ping_ms = sum(pings) / len(pings) if pings else None
            print(f"── {p.name}  {host} via {via}  … testing {a.seconds:.0f}s per direction", flush=True)
            t = RemoteTransport(p.userhost(ip), p.path)
            r = speedtest.run(p.name, host, via, p.userhost(ip), t.ssh_cmd, a.seconds, ping_ms,
                              skip_tcp=a.ssh_only, skip_ssh=a.tcp_only, bulk_ssh_cmd=t.bulk_ssh_cmd)
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
            print(f"peer {p.name:<6} {host} via {via} → ssh ok: {transport_for(p, host).check()}")
        except Exception as e:  # noqa: BLE001
            print(f"peer {p.name:<6} {host} → ssh FAILED: {e}")
            print(f"            fix: ssh-copy-id {p.userhost(host)}   (and enable Remote Login there)")
            ok = False
    print(agent.status())
    return 0 if ok else 1


def icloud_managed(root: Path) -> bool:
    link = Path("~/Library/Mobile Documents/com~apple~CloudDocs/Documents").expanduser()
    try:
        return link.is_symlink() and link.resolve() == root.resolve()
    except OSError:
        return False


def cmd_safetynet(a) -> int:
    cfg = load_config()
    policy = cfg.safetynet_policy()
    print(f"policy: keep {policy[0]} runs, max {cfg.safetynet_max_age_days:g} days, max {cfg.safetynet_max_gb:g} GB per root"
          + ("" if cfg.safetynet else "   (backups disabled: safetynet = false)"))
    over = False
    for root in cfg.effective_roots():
        rc = cfg.for_root(root)
        if a.action == "prune":
            n = safetynet.prune(str(rc.local_root / safetynet.SN_DIR), *policy)
            print(f"   {root.name}: pruned {n} run director{'y' if n == 1 else 'ies'}")
        lines = safetynet.status_lines(rc.local_root, policy)
        over |= any("over policy" in ln for ln in lines)
        print(f"── {root.name}")
        print("\n".join(lines))
    return 1 if over else 0


def register(sub) -> None:
    s = sub.add_parser("tb", help="Thunderbolt ports, link, speed, cabling sketch of this Mac and every peer")
    s.add_argument("--no-remote", action="store_true", help="skip ssh to the peers")
    s.set_defaults(fn=cmd_tb)
    s = sub.add_parser("discover", help="Bonjour scan for Macs with Remote Login")
    s.add_argument("--seconds", type=float, default=3.0)
    s.set_defaults(fn=cmd_discover)
    s = sub.add_parser("speedtest", help="throughput to each peer: raw TCP and the ssh path rsync uses")
    s.add_argument("peers", nargs="*", help="peer names (default: all)")
    s.add_argument("--all", action="store_true")
    s.add_argument("--all-hosts", action="store_true", help="test every reachable host of a peer (Thunderbolt vs LAN)")
    s.add_argument("--seconds", type=float, default=5.0, help="seconds per direction (default 5)")
    s.add_argument("--tcp-only", action="store_true")
    s.add_argument("--ssh-only", action="store_true")
    s.set_defaults(fn=cmd_speedtest)
    sub.add_parser("doctor", help="check rsync, ssh keys, peers").set_defaults(fn=cmd_doctor)
    s = sub.add_parser("safetynet", help="backups made before overwrite: status or prune per root")
    s.add_argument("action", nargs="?", choices=["status", "prune"], default="status")
    s.set_defaults(fn=cmd_safetynet)
