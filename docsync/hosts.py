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


def resolve(peer: Peer, log) -> tuple[str | None, str]:
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
