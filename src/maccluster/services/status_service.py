"""Status snapshot orchestration."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from maccluster.app_factory import AppContext
from maccluster.domain.cable import is_mac_peer_name
from maccluster.domain.enums import LinkState, NodeRole, ReachabilityState
from maccluster.domain.models import (
    TRANSPORT_NAMES,
    HealthSnapshot,
    InterfaceTraffic,
    Node,
    NodeHealth,
)
from maccluster.health.aggregate import exit_code_for_snapshot
from maccluster.health.snapshot import build_snapshot
from maccluster.health.traffic import TrafficSampler
from maccluster.services.config_service import load_and_bind_self
from maccluster.services.sync_history import read_last_run
from maccluster.services.tb_service import probe_tb
from maccluster.services.transport_ladder import arep_peer_for_node, arep_status_json, clean_text
from maccluster.topology.match import best_link_speed, ports_by_peer

# Module-level sampler so consecutive `status` / `monitor` ticks share prev counters.
_SAMPLER: TrafficSampler | None = None


# --- per-peer transport (rdma | tb | wifi | unknown) -------------------------------------


def derive_peer_transport(
    arep_peer: dict[str, Any] | None,
    last_sync_peer: dict[str, Any] | None,
    *,
    bridge_reachable: bool,
) -> tuple[str, str, str]:
    """(transport, source, detail) for one peer.

    The last ``maccluster sync`` run log wins (it records the ladder rung that
    actually moved bytes, plus any downgrade lines); otherwise ``arep status
    --json``: ``lastTransport`` ``rdma`` → rdma, ``tcp`` → arep's own TCP channel,
    which binds to bridge0 when the peer is reachable there (tb) and otherwise
    went another way (wifi); with no transfer yet, ``transportCapable`` says what
    the next one would use. Every peer/arep string is sanitised for the terminal.
    """
    if isinstance(last_sync_peer, dict):
        rung = clean_text(last_sync_peer.get("transport"), 10).lower()
        if rung in TRANSPORT_NAMES:
            downs = last_sync_peer.get("downgrades")
            detail = clean_text(downs[-1]) if isinstance(downs, list) and downs else ""
            return rung, "sync-last", detail
    if isinstance(arep_peer, dict):
        last = clean_text(arep_peer.get("lastTransport"), 10).lower()
        reason = clean_text(arep_peer.get("lastDowngradeReason"), 120)
        why = f"downgrade: {reason}" if reason else ""
        if last == "rdma":
            return "rdma", "arep", why
        tcp_rung = "tb" if bridge_reachable else "wifi"
        if last == "tcp":
            note = "arep tcp channel" + ("" if bridge_reachable else " (bridge unreachable)")
            return tcp_rung, "arep", "; ".join(x for x in (note, why) if x)
        raw = arep_peer.get("transportCapable")
        caps = [c.lower() for c in raw if isinstance(c, str)] if isinstance(raw, list) else []
        if "rdma" in caps:
            return "rdma", "arep", "capable, no transfer yet"
        if "tcp" in caps:
            return tcp_rung, "arep", "tcp capable, no transfer yet"
    return "unknown", "", ""


def _last_sync_peer(last: dict[str, Any] | None, node: Node) -> dict[str, Any] | None:
    peers = last.get("peers") if isinstance(last, dict) else None
    if not isinstance(peers, list):
        return None
    for p in peers:
        if isinstance(p, dict) and (
            p.get("peer_id") == node.id or p.get("peer_ip") == str(node.ip)
        ):
            return p
    return None


def _fetch(source: Callable[[], dict | None] | None, default: Callable[[], dict | None]):
    try:
        data = (source or default)()
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def get_traffic_sampler(*, reset: bool = False) -> TrafficSampler:
    global _SAMPLER
    if reset or _SAMPLER is None:
        _SAMPLER = TrafficSampler(use_disk_cache=True)
    return _SAMPLER


def _traffic_ifaces(bridge_name: str, bridge_members: tuple[str, ...]) -> tuple[str, ...]:
    names = [bridge_name]
    for m in bridge_members:
        if m and m not in names:
            names.append(m)
    # Prefer TB member ports en2/en3/en4 even if not listed yet
    for candidate in ("en2", "en3", "en4"):
        if candidate not in names:
            names.append(candidate)
    return tuple(names)


def collect_traffic(
    ctx: AppContext,
    *,
    bridge_name: str,
    bridge_members: tuple[str, ...] = (),
    sampler: TrafficSampler | None = None,
    persist: bool = True,
) -> tuple[InterfaceTraffic, ...]:
    """Collect counters + rates for bridge and TB-related ifaces."""
    samp = sampler or get_traffic_sampler()
    want = _traffic_ifaces(bridge_name, bridge_members)
    try:
        current = ctx.net_read.get_iface_counters_many(want)
    except Exception:
        current = {}
    # Prefer bridge + members; drop idle en* that aren't members
    if bridge_members:
        filtered = {
            k: v
            for k, v in current.items()
            if k == bridge_name or k in bridge_members or (v.ibytes + v.obytes) > 0
        }
        if filtered:
            current = filtered
    elif current:
        # No members yet: still show bridge0 if present
        if bridge_name in current:
            current = {bridge_name: current[bridge_name]}
    return samp.observe(current, persist=persist)


def collect_status(
    ctx: AppContext,
    *,
    sampler: TrafficSampler | None = None,
    persist_traffic: bool = True,
    include_exo: bool = False,
    exo_base_url: str | None = None,
    arep_status: Callable[[], dict | None] | None = None,
    last_sync: Callable[[], dict | None] | None = None,
) -> tuple[HealthSnapshot, int]:
    cfg, self_node = load_and_bind_self(ctx)
    try:
        tb = probe_tb(ctx)
    except Exception:
        tb = None
    # Both sources are read once per snapshot and never raise into status.
    arep = _fetch(arep_status, arep_status_json)
    last = _fetch(last_sync, read_last_run)

    bridge = None
    try:
        bridge = ctx.net_read.get_bridge(cfg.bridge_interface)
    except Exception:
        bridge = None

    members = bridge.members if bridge else ()
    traffic = collect_traffic(
        ctx,
        bridge_name=cfg.bridge_interface,
        bridge_members=members,
        sampler=sampler,
        persist=persist_traffic,
    )

    # Local link summary
    local_link = LinkState.UNKNOWN
    if tb and tb.ports:
        if any(p.link_state == LinkState.CONNECTED for p in tb.ports):
            local_link = LinkState.CONNECTED
        elif all(p.link_state == LinkState.UNCONNECTED for p in tb.ports):
            local_link = LinkState.UNCONNECTED

    # Each peer row shows ITS link's negotiated rate (controller UID / domain
    # UUID / hostname mapping), never the machine's best Mac↔Mac link.
    peer_nodes = tuple(n for n in cfg.nodes if n.id != self_node.id and n.role != NodeRole.SELF)
    peer_tb_ports = ports_by_peer(tb=tb, peers=peer_nodes)

    node_health: list[NodeHealth] = []
    for node in cfg.nodes:
        if node.id == self_node.id or node.role == NodeRole.SELF:
            node_health.append(
                NodeHealth(
                    node=node,
                    reachability=ReachabilityState.UP,
                    link_state=local_link,
                    notes="self",
                    transport="self",
                )
            )
            continue
        # Peer reachability: ICMP from self IP (TB bridge), then TCP:22, optional SSH
        from maccluster.health.reach import probe_peer

        peer_ip = str(node.ip)
        source = str(self_node.ip)
        pr = probe_peer(ctx, peer_ip=peer_ip, source=source)
        state = pr.state
        rtt = pr.rtt_ms
        notes = pr.method

        if cfg.ssh_probes_enabled and state != ReachabilityState.UP:
            target = node.ssh_target or peer_ip
            try:
                sr = ctx.reachability.ssh_probe(target)
                if sr.state == ReachabilityState.UP:
                    state = ReachabilityState.UP
                    rtt = sr.rtt_ms
                    notes = "ssh"
            except Exception:
                pass

        # This peer's own TB link (matched port); rate only when attributable.
        peer_link = LinkState.UNKNOWN
        peer_speed: float | None = None
        matched_ports = peer_tb_ports.get(node.id, ())
        if matched_ports:
            peer_link = LinkState.CONNECTED
            peer_speed = best_link_speed(matched_ports)
        elif (
            tb
            and tb.ports
            and state == ReachabilityState.UP
            and any(
                p.link_state == LinkState.CONNECTED and is_mac_peer_name(p.peer_name)
                for p in tb.ports
            )
        ):
            # A Mac↔Mac link exists but can't be pinned to this peer:
            # report the state without borrowing another peer's rate.
            peer_link = LinkState.CONNECTED

        transport, t_source, t_detail = derive_peer_transport(
            arep_peer_for_node(arep, node),
            _last_sync_peer(last, node),
            bridge_reachable=state == ReachabilityState.UP,
        )
        node_health.append(
            NodeHealth(
                node=node,
                reachability=state,
                link_state=peer_link,
                link_speed_gbps=peer_speed,
                rtt_ms=rtt,
                notes=notes,
                transport=transport,
                transport_source=t_source,
                transport_detail=t_detail,
            )
        )

    from maccluster.adapters.rdma_ctl import probe_rdma
    from maccluster.services.heal_heartbeat import read_heartbeat

    try:
        rdma = probe_rdma(ctx.runner)
    except Exception:
        rdma = None

    try:
        heal_hb = read_heartbeat(interval_seconds=float(cfg.heal_interval_seconds))
    except Exception:
        heal_hb = None

    exo = None
    if include_exo:
        from maccluster.services.exo_correlator import probe_exo

        exo = probe_exo(
            base_url=exo_base_url,
            expected_nodes=len(cfg.nodes),
        )

    snap = build_snapshot(
        timestamp=ctx.clock.now(),
        cfg=cfg,
        self_node_id=self_node.id,
        node_health=node_health,
        bridge=bridge,
        tb=tb,
        traffic=traffic,
        rdma=rdma,
        heal_heartbeat=heal_hb,
        exo=exo,
    )
    return snap, exit_code_for_snapshot(snap)
