"""Doctor diagnostics orchestration."""

from __future__ import annotations

from maccluster.app_factory import AppContext
from maccluster.doctor_logic import checks
from maccluster.doctor_logic.report import build_report
from maccluster.domain.models import DoctorReport
from maccluster.services.config_service import load_and_bind_self, load_config
from maccluster.services.tb_service import probe_tb


def run_doctor(ctx: AppContext) -> DoctorReport:
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

    peers: list = []
    if cfg is not None and self_node is not None:
        from maccluster.health.reach import probe_peer

        source = str(self_node.ip)
        for node in cfg.nodes:
            if node.id == self_node.id:
                continue
            state = probe_peer(ctx, peer_ip=str(node.ip), source=source).state
            peers.append((node, state))
    findings.append(checks.check_peers(peers))

    iperf_ok = False
    if ctx.bench is not None:
        try:
            iperf_ok = ctx.bench.available()
        except Exception:
            iperf_ok = False
    findings.append(checks.check_iperf(iperf_ok))

    return build_report(findings)
