"""Plaintext renderers for CLI commands."""

from __future__ import annotations

from maccluster.domain.enums import NodeRole
from maccluster.domain.models import (
    ClusterConfig,
    DoctorReport,
    HealthSnapshot,
    InterfaceTraffic,
    ServiceState,
    ThunderboltSnapshot,
    Topology,
)
from maccluster.health.traffic import format_bps, format_pps
from maccluster.render.sanitize import sanitize
from maccluster.render.symbols import link_symbol, reachability_symbol


def render_tb(snap: ThunderboltSnapshot) -> str:
    lines = [f"Thunderbolt ({snap.source})" + (f" — {snap.host_model}" if snap.host_model else "")]
    if not snap.ports:
        lines.append("  (no ports detected)")
        return "\n".join(lines)
    for p in snap.ports:
        speed = f"{p.link_speed_gbps:g} Gb/s" if p.link_speed_gbps is not None else "n/a"
        peer = sanitize(p.peer_name) if p.peer_name else "no peer"
        iface = p.interface_name or "-"
        lines.append(
            f"  receptacle {sanitize(p.receptacle_id)}: "
            f"{link_symbol(p.link_state)} {p.link_state.value} "
            f"cap={p.thunderbolt_version or '?'} speed={speed} "
            f"iface={iface} peer={peer}"
        )
        if p.domain_uuid:
            lines.append(f"    domain={p.domain_uuid}")
    return "\n".join(lines)


def render_config(cfg: ClusterConfig, *, self_id: str | None = None) -> str:
    lines = [
        f"name: {sanitize(cfg.name)}",
        f"schema_version: {cfg.schema_version}",
        f"subnet: {cfg.subnet}",
        f"bridge_interface: {cfg.bridge_interface}",
        f"heal_interval_seconds: {cfg.heal_interval_seconds}",
        f"ssh_probes_enabled: {cfg.ssh_probes_enabled}",
        f"nodes ({len(cfg.nodes)}):",
    ]
    for n in cfg.nodes:
        role = ""
        if self_id and n.id == self_id:
            role = " role=self"
        elif n.role == NodeRole.SELF:
            role = " role=self"
        elif n.role == NodeRole.PEER:
            role = " role=peer"
        hosts = ", ".join(sanitize(h) for h in n.hostnames)
        lines.append(f"  - {n.id}: ip={n.ip} hosts=[{hosts}] hw_uuid={n.hw_uuid}{role}")
    return "\n".join(lines)


def render_traffic_block(traffic: tuple[InterfaceTraffic, ...]) -> list[str]:
    if not traffic:
        return ["traffic: (no interface counters)"]
    rated = [t for t in traffic if t.rate_available and t.sample_dt_s is not None]
    if rated:
        dt_note = f" Δ{rated[0].sample_dt_s:.1f}s"
    else:
        dt_note = " (rates after 2nd sample within ~2 min)"
    lines = [f"traffic{dt_note}:"]
    for t in traffic:
        if t.rate_available:
            err = (
                f"err in/out {t.ierrs}/{t.oerrs} "
                f"(+{t.ierrs_delta or 0}/+{t.oerrs_delta or 0})"
            )
            lines.append(
                f"  {t.name:10}  "
                f"RX {format_bps(t.rx_bps):>10} ({format_pps(t.rx_pps):>8})  "
                f"TX {format_bps(t.tx_bps):>10} ({format_pps(t.tx_pps):>8})  "
                f"{err}"
            )
        else:
            lines.append(
                f"  {t.name:10}  "
                f"RX bytes={t.ibytes} pkts={t.ipkts}  "
                f"TX bytes={t.obytes} pkts={t.opkts}  "
                f"err in/out {t.ierrs}/{t.oerrs}  rate=n/a"
            )
    return lines


def render_status(snap: HealthSnapshot) -> str:
    lines = [
        f"cluster: {sanitize(snap.cluster_name)}  overall={snap.overall.value}  "
        f"ts={snap.timestamp.isoformat()}",
    ]
    if snap.bridge:
        b = snap.bridge
        addrs = ",".join(str(a) for a in b.addresses) or "-"
        lines.append(f"bridge: {b.name} exists={b.exists} up={b.admin_up} addrs={addrs}")
    for nh in snap.nodes:
        mark = "*" if snap.self_node_id and nh.node.id == snap.self_node_id else " "
        rtt = f" rtt={nh.rtt_ms:.1f}ms" if nh.rtt_ms is not None else ""
        lines.append(
            f"{mark} {nh.node.id:12} {str(nh.node.ip):15} "
            f"{reachability_symbol(nh.reachability)} {nh.reachability.value:7} "
            f"{link_symbol(nh.link_state)} {nh.link_state.value}{rtt}"
        )
    lines.extend(render_traffic_block(snap.traffic))
    return "\n".join(lines)


def render_topo(topo: Topology) -> str:
    lines = [f"topology complete={topo.complete}"]
    for lnk in topo.links:
        match = lnk.matched_node_id or "-"
        peer = sanitize(lnk.peer_hint) if lnk.peer_hint else "-"
        speed = f"{lnk.speed_gbps:g}G" if lnk.speed_gbps is not None else "-"
        lines.append(
            f"  receptacle {sanitize(lnk.local_receptacle)}: "
            f"{link_symbol(lnk.link_state)} {lnk.link_state.value} "
            f"peer={peer} matched={match} speed={speed}"
        )
        if lnk.domain_uuid:
            lines.append(f"    domain={lnk.domain_uuid}")
    if topo.unmatched_peers:
        lines.append("unmatched peers: " + ", ".join(sanitize(p) for p in topo.unmatched_peers))
    # Explicitly no cable routing advice (A-023)
    return "\n".join(lines)


def render_doctor(report: DoctorReport) -> str:
    lines = [f"doctor worst={report.worst.value}"]
    for f in report.findings:
        detail = f" — {sanitize(f.detail)}" if f.detail else ""
        lines.append(f"  [{f.severity.value:7}] {f.check_id}: {sanitize(f.summary)}{detail}")
    return "\n".join(lines)


def render_service(state: ServiceState) -> str:
    return (
        f"label: {state.label}\n"
        f"installed: {state.installed}\n"
        f"running: {state.running}\n"
        f"plist: {state.plist_path or '-'}\n"
        f"interval_seconds: {state.interval_seconds if state.interval_seconds is not None else '-'}\n"
        f"detail: {sanitize(state.detail)}"
    )
