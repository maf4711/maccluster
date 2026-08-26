"""Shared peer reachability policy (status / doctor / topo)."""

from __future__ import annotations

from maccluster.app_factory import AppContext
from maccluster.domain.enums import ReachabilityState
from maccluster.ports.reachability import ReachabilityResult


def probe_peer(
    ctx: AppContext,
    *,
    peer_ip: str,
    source: str,
) -> ReachabilityResult:
    """ICMP from self cluster IP, then TCP:22 if ICMP fails/filtered."""
    last = ReachabilityResult(
        target=peer_ip,
        state=ReachabilityState.UNKNOWN,
        method="none",
    )
    try:
        last = ctx.reachability.ping(peer_ip, source=source)
        if last.state == ReachabilityState.UP:
            return last
    except Exception as exc:
        last = ReachabilityResult(
            target=peer_ip,
            state=ReachabilityState.UNKNOWN,
            method="ping",
            detail=str(exc)[:120],
        )
    try:
        tr = ctx.reachability.tcp_probe(peer_ip, port=22)
        if tr.state == ReachabilityState.UP:
            return tr
        if last.state == ReachabilityState.UNKNOWN:
            return tr
    except Exception:
        pass
    return last
