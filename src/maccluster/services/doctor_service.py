"""Doctor diagnostics orchestration."""

from __future__ import annotations

from maccluster.adapters.rdma_ctl import probe_rdma
from maccluster.app_factory import AppContext
from maccluster.constants import LAUNCH_AGENT_LABEL
from maccluster.doctor_logic import checks
from maccluster.doctor_logic.report import build_report
from maccluster.domain.enums import CheckSeverity, ReachabilityState
from maccluster.domain.models import DoctorFinding, DoctorReport, NodeHealth
from maccluster.health.mesh import build_mesh_health
from maccluster.services.config_service import load_and_bind_self, load_config
from maccluster.services.heal_heartbeat import read_heartbeat
from maccluster.services.tb_service import probe_tb
from maccluster.services.transport_ladder import arep_status_json


def run_doctor(
    ctx: AppContext,
    *,
    include_exo: bool = False,
    exo_base_url: str | None = None,
    include_host: bool = False,
    include_fleet: bool = False,
    peer: str | None = None,
) -> DoctorReport:
    findings = []
    cfg = None
    self_node = None
    load_error = None
    self_error = None
    try:
        cfg, self_node = load_and_bind_self(ctx)
    except Exception as exc:
        load_error = str(exc)
        try:
            cfg = load_config(ctx)
        except Exception as exc2:
            load_error = str(exc2)
            cfg = None
        self_error = str(exc)

    findings.append(checks.check_config(cfg, load_error if cfg is None else None))
    if cfg is not None and self_node is None and self_error:
        findings.append(checks.check_self(None, self_error))
    else:
        findings.append(checks.check_self(self_node, self_error if self_node is None else None))

    tb = None
    tb_error = None
    try:
        tb = probe_tb(ctx)
    except Exception as exc:
        tb_error = str(exc)
    findings.append(checks.check_tb(tb, tb_error))
    findings.append(checks.check_cable(tb))

    bridge = None
    desired_ip = str(self_node.ip) if self_node else None
    if cfg is not None:
        try:
            bridge = ctx.net_read.get_bridge(cfg.bridge_interface)
        except Exception:
            bridge = None
    findings.append(checks.check_bridge(bridge, desired_ip))
    try:
        info = ctx.runner.run(
            ["/usr/sbin/networksetup", "-getinfo", "Thunderbolt Bridge"],
            timeout=5,
        )
        from maccluster.services.wifi_guard import parse_networksetup_info

        router = parse_networksetup_info(info.stdout or "").get("Router")
    except Exception:
        router = None
    findings.append(checks.check_tb_gateway(router))

    peers: list = []
    node_health: list[NodeHealth] = []
    if cfg is not None and self_node is not None:
        from maccluster.health.reach import probe_peer

        source = str(self_node.ip)
        for node in cfg.nodes:
            if node.id == self_node.id:
                node_health.append(
                    NodeHealth(node=node, reachability=ReachabilityState.UP, notes="self")
                )
                continue
            state = probe_peer(ctx, peer_ip=str(node.ip), source=source).state
            peers.append((node, state))
            node_health.append(NodeHealth(node=node, reachability=state))
    findings.append(checks.check_peers(peers))

    mesh = build_mesh_health(
        node_health,
        self_node_id=self_node.id if self_node else None,
        bridge=bridge,
        tb=tb,
    )
    findings.append(checks.check_mesh(mesh))

    try:
        rdma = probe_rdma(ctx.runner)
    except Exception:
        rdma = None
    findings.append(checks.check_rdma(rdma))
    arep_peers: list[dict] = []
    if rdma is not None and rdma.enabled is True:
        # Only worth asking arep when the OS has RDMA on; never raises.
        status = arep_status_json(runner=ctx.runner)
        raw = status.get("peers") if isinstance(status, dict) else None
        arep_peers = [p for p in raw if isinstance(p, dict)] if isinstance(raw, list) else []
    findings.append(checks.check_rdma_device_to_peer(rdma, arep_peers))

    service_installed = False
    try:
        st = ctx.service.status(label=LAUNCH_AGENT_LABEL)
        service_installed = bool(st.installed)
    except Exception:
        service_installed = False
    interval = float(cfg.heal_interval_seconds) if cfg else None
    try:
        hb = read_heartbeat(interval_seconds=interval)
    except Exception:
        hb = None
    findings.append(checks.check_heal_heartbeat(hb, service_installed=service_installed))

    if include_exo:
        from maccluster.services.exo_correlator import probe_exo

        expected = len(cfg.nodes) if cfg else None
        exo = probe_exo(base_url=exo_base_url, expected_nodes=expected)
        findings.append(checks.check_exo(exo))

    iperf_ok = False
    if ctx.bench is not None:
        try:
            iperf_ok = ctx.bench.available()
        except Exception:
            iperf_ok = False
    findings.append(checks.check_iperf(iperf_ok))

    if include_host:
        from maccluster.services.doctor_host import (
            collect_fleet,
            collect_local,
            findings_from_snapshot,
        )

        self_id = self_node.id if self_node else "self"
        local = collect_local(ctx, self_id)
        findings.extend(findings_from_snapshot(local, peer=False))
        if include_fleet:
            if cfg is None or self_node is None:
                findings.append(
                    DoctorFinding(
                        "host",
                        CheckSeverity.WARN,
                        "fleet host needs config and self",
                        "",
                    )
                )
            else:
                for snap in collect_fleet(ctx, cfg, self_node, peer=peer):
                    findings.extend(findings_from_snapshot(snap, peer=True))

    return build_report(findings)
