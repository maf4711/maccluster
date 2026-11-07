"""Plaintext renderers for CLI commands."""

from __future__ import annotations

from maccluster.domain.enums import NodeRole
from maccluster.domain.models import (
    ClusterConfig,
    DoctorReport,
    HealthSnapshot,
    InterfaceTraffic,
    MeshBenchReport,
    RdmaStatus,
    ServiceState,
    ThunderboltSnapshot,
    Topology,
)
from maccluster.health.traffic import format_bps, format_pps
from maccluster.render.sanitize import sanitize
from maccluster.render.symbols import link_symbol, reachability_symbol
from maccluster.services.fleet_heal_service import FleetHealReport


def render_tb(snap: ThunderboltSnapshot, *, rdma: RdmaStatus | None = None) -> str:
    from maccluster.domain.cable import assess_cluster_cables

    lines = [f"Thunderbolt ({snap.source})" + (f" — {snap.host_model}" if snap.host_model else "")]
    if not snap.ports:
        lines.append("  (no ports detected)")
    else:
        for p in snap.ports:
            speed = f"{p.link_speed_gbps:g} Gb/s" if p.link_speed_gbps is not None else "n/a"
            peer = sanitize(p.peer_name) if p.peer_name else "no peer"
            iface = p.interface_name or "-"
            mode = f" mode={sanitize(p.peer_mode)}" if p.peer_mode else ""
            lines.append(
                f"  receptacle {sanitize(p.receptacle_id)}: "
                f"{link_symbol(p.link_state)} {p.link_state.value} "
                f"cap={p.thunderbolt_version or '?'} speed={speed} "
                f"iface={iface} peer={peer}{mode}"
            )
            if p.domain_uuid:
                lines.append(f"    domain={p.domain_uuid}")
        report = assess_cluster_cables(snap)
        lines.append(f"cable: [{report.overall_grade.value}] {report.summary}")
        lines.append(f"  → {report.recommendation}")
    if rdma is not None:
        en = (
            "enabled"
            if rdma.enabled is True
            else "disabled"
            if rdma.enabled is False
            else "unknown"
        )
        avail = "yes" if rdma.tool_available else "no"
        lines.append(f"rdma: tool={avail} status={en} — {sanitize(rdma.detail)}")
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
            err = f"err in/out {t.ierrs}/{t.oerrs} (+{t.ierrs_delta or 0}/+{t.oerrs_delta or 0})"
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
    from maccluster.domain.cable import assess_cluster_cables

    lines = [
        f"cluster: {sanitize(snap.cluster_name)}  overall={snap.overall.value}  "
        f"ts={snap.timestamp.isoformat()}",
    ]
    if snap.mesh is not None:
        m = snap.mesh
        lines.append(
            f"mesh: [{m.verdict.value}] {m.summary} "
            f"(alive≠meshed: bridge_ok={m.bridge_ok} tb_links={m.tb_links})"
        )
    if snap.bridge:
        b = snap.bridge
        addrs = ",".join(str(a) for a in b.addresses) or "-"
        lines.append(f"bridge: {b.name} exists={b.exists} up={b.admin_up} addrs={addrs}")
    if snap.tb is not None:
        cable = assess_cluster_cables(snap.tb)
        lines.append(
            f"cable: [{cable.overall_grade.value}] {cable.summary}"
            + (
                f" (best {cable.best_mac_peer_gbps:g}G)"
                if cable.best_mac_peer_gbps is not None
                else ""
            )
        )
    if snap.rdma is not None:
        en = (
            "enabled"
            if snap.rdma.enabled is True
            else "disabled"
            if snap.rdma.enabled is False
            else "n/a"
        )
        lines.append(f"rdma: {en} — {sanitize(snap.rdma.detail)}")
    if snap.heal_heartbeat is not None:
        hb = snap.heal_heartbeat
        stale = "stale" if hb.stale else "fresh"
        lines.append(f"heal: heartbeat={stale} — {sanitize(hb.detail)}")
    for nh in snap.nodes:
        mark = "*" if snap.self_node_id and nh.node.id == snap.self_node_id else " "
        rtt = f" rtt={nh.rtt_ms:.1f}ms" if nh.rtt_ms is not None else ""
        how = f" via={sanitize(nh.notes)}" if nh.notes and nh.notes not in ("self",) else ""
        spd = f" {nh.link_speed_gbps:g}G" if nh.link_speed_gbps is not None else ""
        is_self = nh.transport == "self" or (
            snap.self_node_id is not None and nh.node.id == snap.self_node_id
        )
        tp = "" if is_self else f" transport={sanitize(nh.transport or 'unknown')}"
        lines.append(
            f"{mark} {nh.node.id:12} {str(nh.node.ip):15} "
            f"{reachability_symbol(nh.reachability)} {nh.reachability.value:7} "
            f"{link_symbol(nh.link_state)} {nh.link_state.value}{spd}{rtt}{how}{tp}"
        )
        if not is_self and nh.transport_detail:
            src = f"{sanitize(nh.transport_source)}: " if nh.transport_source else ""
            lines.append(f"    transport {src}{sanitize(nh.transport_detail)}")
    if snap.exo is not None:
        lines.append(f"exo: {sanitize(snap.exo.summary)}")
        if snap.exo.instances_summary:
            lines.append(f"  workload: {sanitize(snap.exo.instances_summary)}")
        if snap.exo.error and not snap.exo.http_ok:
            lines.append(f"  note: {sanitize(snap.exo.error)}")
    lines.extend(render_traffic_block(snap.traffic))
    return "\n".join(lines)


def render_topo(topo: Topology) -> str:
    lines = [f"topology complete={topo.complete}"]
    for lnk in topo.links:
        match = lnk.matched_node_id or "-"
        by = f" by={sanitize(lnk.matched_by)}" if lnk.matched_node_id and lnk.matched_by else ""
        peer = sanitize(lnk.peer_hint) if lnk.peer_hint else "-"
        speed = f"{lnk.speed_gbps:g}G" if lnk.speed_gbps is not None else "-"
        lines.append(
            f"  receptacle {sanitize(lnk.local_receptacle)}: "
            f"{link_symbol(lnk.link_state)} {lnk.link_state.value} "
            f"peer={peer} matched={match}{by} speed={speed}"
        )
        ids = []
        if lnk.domain_uuid:
            ids.append(f"domain={sanitize(lnk.domain_uuid)}")
        if lnk.peer_domain_uuid:
            ids.append(f"peer_domain={sanitize(lnk.peer_domain_uuid)}")
        if lnk.peer_uid:
            ids.append(f"peer_uid={sanitize(lnk.peer_uid)}")
        if ids:
            lines.append("    " + " ".join(ids))
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


def render_mesh_bench(report: MeshBenchReport) -> str:
    orch = "yes" if report.orchestrated else "no"
    lines = [f"mesh bench  Δ{report.duration_s}s  path={report.bind_mode}  orchestrated={orch}"]
    if report.busy_skipped:
        lines.append(sanitize(report.summary))
        return "\n".join(lines)
    for p in report.paths:
        thr = f"{p.mbps:.0f} Mbit/s" if p.mbps is not None else "n/a"
        extra = ""
        if p.retransmits is not None:
            extra += f"  retransmits={p.retransmits}"
        if p.flags:
            extra += "  flags=" + ",".join(p.flags)
        if p.reverse:
            extra += "  reverse"
        lines.append(f"  {p.src_id} → {p.dst_id}  {thr}  quality={p.quality.value}{extra}")
        if not p.ok and p.message:
            lines.append(f"    {sanitize(p.message)}")
    lines.append(f"summary: {sanitize(report.summary)}")
    return "\n".join(lines)


def render_fleet_heal(report: FleetHealReport, *, dry_run: bool = False) -> str:
    mode = "dry-run" if dry_run else "apply"
    tog = " together" if report.together else ""
    lines = [f"heal --fleet ({mode}{tog})  {sanitize(report.summary)}"]
    if report.self_result is not None:
        lines.append(f"  self: {sanitize(report.self_result.message)}")
    elif report.self_degraded:
        lines.append("  self: degraded")
    for hop in report.hops:
        if hop.ok:
            state = "ok"
        elif hop.skipped:
            state = "skipped"
        else:
            state = "fail"
        extra = f" — {sanitize(hop.message)}" if hop.message else ""
        lines.append(f"  {hop.node_id} ({hop.peer_ip}): {state}{extra}")
    return "\n".join(lines)
