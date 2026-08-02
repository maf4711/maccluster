"""Status snapshot orchestration."""

from __future__ import annotations

from maccluster.app_factory import AppContext
from maccluster.domain.enums import LinkState, NodeRole, ReachabilityState
from maccluster.domain.models import HealthSnapshot, InterfaceTraffic, NodeHealth
from maccluster.health.aggregate import exit_code_for_snapshot
from maccluster.health.snapshot import build_snapshot
from maccluster.health.traffic import TrafficSampler
from maccluster.services.config_service import load_and_bind_self
from maccluster.services.tb_service import probe_tb

# Module-level sampler so consecutive `status` / `monitor` ticks share prev counters.
_SAMPLER: TrafficSampler | None = None


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
) -> tuple[HealthSnapshot, int]:
    cfg, self_node = load_and_bind_self(ctx)
    try:
        tb = probe_tb(ctx)
    except Exception:
        tb = None

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

    node_health: list[NodeHealth] = []
    for node in cfg.nodes:
        if node.id == self_node.id or node.role == NodeRole.SELF:
            node_health.append(
                NodeHealth(
                    node=node,
                    reachability=ReachabilityState.UP,
                    link_state=local_link,
                    notes="self",
                )
            )
            continue
        # Peer reachability: ICMP from self IP (TB bridge), then TCP:22, optional SSH
        state = ReachabilityState.UNKNOWN
        rtt: float | None = None
        notes = ""
        peer_ip = str(node.ip)
        source = str(self_node.ip)
        try:
            pr = ctx.reachability.ping(peer_ip, source=source)
            state = pr.state
            rtt = pr.rtt_ms
            notes = pr.method
        except Exception as exc:
            notes = f"ping-error:{exc}"[:80]

        if state != ReachabilityState.UP:
            try:
                tr = ctx.reachability.tcp_probe(peer_ip, port=22)
                if tr.state == ReachabilityState.UP:
                    state = ReachabilityState.UP
                    rtt = tr.rtt_ms
                    notes = tr.method
            except Exception:
                pass

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

        node_health.append(
            NodeHealth(
                node=node,
                reachability=state,
                link_state=LinkState.UNKNOWN,
                rtt_ms=rtt,
                notes=notes,
            )
        )

    snap = build_snapshot(
        timestamp=ctx.clock.now(),
        cfg=cfg,
        self_node_id=self_node.id,
        node_health=node_health,
        bridge=bridge,
        tb=tb,
        traffic=traffic,
    )
    return snap, exit_code_for_snapshot(snap)
