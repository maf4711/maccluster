"""Peer host resolution and transport construction, shared by the CLI and docsync.api."""

from __future__ import annotations

import sys
from pathlib import Path

from docsync import discover
from docsync.config import CONFIG_FILE, Config, Peer
from docsync.transport import LocalTransport, RemoteTransport, TransportError


def transport_for(peer: Peer, host: str, path: str | None = None, bind: str | None = None):
    """Transport to `peer` at `host` for the peer-side folder `path` (default: the peer's own path)."""
    if host == "/":  # "this machine": the peer folder is the given path
        return LocalTransport(Path(path or peer.path).expanduser())
    if host.startswith("/"):  # a directory on this machine (mounted volume, test tree)
        return LocalTransport(Path(host))
    return RemoteTransport(peer.userhost(host), path or peer.path, bind=bind)


def _probe(host: str, via: str, log) -> tuple[str, str] | None:
    if host.startswith("/"):
        log(f"   {host:<28} not a directory")
        return None
    if via.endswith("(link down)"):
        log(f"   {host:<28} skipped — {via}")
        return None
    ms = discover.ping(host)
    log(f"   {host:<28} {'%.1f ms  via %s' % (ms, via) if ms is not None else 'unreachable'}")
    return (host, via) if ms is not None else None


def _tb_unconfigured(peer: Peer, log) -> tuple[str, str] | None:
    """Peer reachable on the Thunderbolt bridge but not at its configured address.

    The cluster /24 (10.42.0.0/24) needs root on bridge0. Without it the cable is still up and
    the peer answers at an IPv6 link-local (NDP) or, less often, an IPv4 ARP address. Those are
    tried before any LAN name. A neighbour is accepted only when `hostname -s` matches this peer.
    """
    wanted = {discover.base_name(h) for h in peer.hosts if not h.startswith("/")}
    seen = set(peer.hosts)
    link_locals = discover.tb_link_locals() or discover.tb_hostname_addrs(peer.hosts)
    candidates = [(ip, "Thunderbolt (link-local)") for ip in link_locals]
    candidates += [(ip, "Thunderbolt (ARP)") for ip in discover.tb_neighbours()]
    for ip, via in candidates:
        if ip in seen:
            continue
        seen.add(ip)
        try:
            name = RemoteTransport(peer.userhost(ip), peer.path).ssh("hostname -s", timeout=8).stdout.strip()
        except Exception:  # noqa: BLE001 — one dead neighbour must not hide the LAN
            log(f"   {ip:<28} unreachable")
            continue
        if name and discover.base_name(name) in wanted:
            log(f"   {ip:<28} {via}, answers as {name}")
            return ip, via
        log(f"   {ip:<28} Thunderbolt neighbour is {name or '?'} — not this peer")
    return None


def resolve(peer: Peer, log) -> tuple[str | None, str]:
    """Pick the first reachable host: configured Thunderbolt, then a live TB neighbour
    (IPv6 link-local, then ARP), then LAN, then Bonjour. Returns (host, transport)."""
    for h in peer.hosts:  # a directory on this Mac (mounted volume, local mirror) is never pinged
        if h.startswith("/") and Path(h).expanduser().is_dir():
            log(f"   {h:<28} local directory")
            return h, "local directory"
    ordered = discover.order_hosts(peer.hosts)
    for h, via in ordered:
        if via != "Thunderbolt":
            if via.endswith("(link down)"):
                log(f"   {h:<28} skipped — {via}")
            continue
        hit = _probe(h, via, log)
        if hit:
            return hit
    hit = _tb_unconfigured(peer, log)
    if hit:
        return hit
    for h, via in ordered:
        if via == "Thunderbolt" or via.endswith("(link down)"):
            continue
        hit = _probe(h, via, log)
        if hit:
            return hit
    host = discover.bonjour_fallback(peer.hosts)
    if host:
        log(f"   {host:<28} found via Bonjour (name suffix changed)")
        return host, "LAN (Bonjour)"
    return None, ""


def resolve_peer(peer: Peer) -> tuple[str | None, str]:
    return resolve(peer, lambda *a: None)


def select_peers(cfg: Config, names: list[str], all_: bool) -> list[Peer]:
    if all_ or not names:
        if not cfg.peers:
            sys.exit("no peers configured — run: docsync init --from-maccluster  or edit " + str(CONFIG_FILE))
        return list(cfg.peers)
    return [cfg.peer(n) for n in names]



class Unreachable:
    def __init__(self, peer: Peer):
        self.peer = peer

    def check(self) -> str:
        raise TransportError(f"none of {', '.join(self.peer.hosts)} answered a ping")
