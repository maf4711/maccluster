"""Doctor check builders (pure findings from inputs)."""

from __future__ import annotations

from maccluster.config.validate import validate_config
from maccluster.domain.enums import CheckSeverity, LinkState, ReachabilityState
from maccluster.domain.models import (
    BridgeInterface,
    ClusterConfig,
    DoctorFinding,
    Node,
    ThunderboltSnapshot,
)


def check_config(cfg: ClusterConfig | None, load_error: str | None = None) -> DoctorFinding:
    if load_error:
        return DoctorFinding("config", CheckSeverity.ERROR, "config load failed", load_error)
    if cfg is None:
        return DoctorFinding("config", CheckSeverity.ERROR, "config missing", "run maccluster init")
    errors = validate_config(cfg)
    if errors:
        return DoctorFinding(
            "config",
            CheckSeverity.ERROR,
            "config invalid",
            "; ".join(errors),
        )
    return DoctorFinding("config", CheckSeverity.OK, "config valid", cfg.name)


def check_self(self_node: Node | None, error: str | None = None) -> DoctorFinding:
    if error:
        return DoctorFinding("self", CheckSeverity.ERROR, "self-match failed", error)
    if self_node is None:
        return DoctorFinding("self", CheckSeverity.ERROR, "self unknown", "")
    return DoctorFinding(
        "self",
        CheckSeverity.OK,
        f"self={self_node.id}",
        f"ip={self_node.ip}",
    )


def check_tb(tb: ThunderboltSnapshot | None, error: str | None = None) -> DoctorFinding:
    if error:
        return DoctorFinding("thunderbolt", CheckSeverity.WARN, "TB probe failed", error)
    if tb is None or not tb.ports:
        return DoctorFinding(
            "thunderbolt",
            CheckSeverity.WARN,
            "no TB ports detected",
            "",
        )
    connected = sum(1 for p in tb.ports if p.link_state == LinkState.CONNECTED)
    return DoctorFinding(
        "thunderbolt",
        CheckSeverity.OK if tb.ports else CheckSeverity.WARN,
        f"{len(tb.ports)} port(s), {connected} connected",
        tb.source,
    )


def check_cable(tb: ThunderboltSnapshot | None) -> DoctorFinding:
    """Grade TB cable/path: 40G excellent, 20G ok, below = warn."""
    from maccluster.domain.cable import CableGrade, assess_cluster_cables

    report = assess_cluster_cables(tb)
    if report.overall_grade == CableGrade.EXCELLENT:
        sev = CheckSeverity.OK
    elif report.overall_grade == CableGrade.GOOD:
        sev = CheckSeverity.OK
    elif report.overall_grade == CableGrade.MARGINAL:
        sev = CheckSeverity.WARN
    else:
        sev = CheckSeverity.WARN
    return DoctorFinding(
        "cable",
        sev,
        report.summary,
        report.recommendation,
    )


def check_tb_gateway(router: str | None) -> DoctorFinding:
    """WARN when Thunderbolt Bridge has a Router (kills Wi-Fi internet)."""
    from maccluster.services.wifi_guard import router_steals_internet

    if not router_steals_internet(router):
        return DoctorFinding(
            "tb_gateway",
            CheckSeverity.OK,
            "TB Bridge has no default router",
            "Wi-Fi keeps internet",
        )
    return DoctorFinding(
        "tb_gateway",
        CheckSeverity.WARN,
        f"TB Bridge Router={router} steals default route",
        "sudo maccluster up  (clears router, Wi-Fi first)",
    )


def check_bridge(bridge: BridgeInterface | None, desired_ip: str | None) -> DoctorFinding:
    if bridge is None:
        return DoctorFinding("bridge", CheckSeverity.WARN, "bridge not probed", "")
    if not bridge.exists:
        return DoctorFinding(
            "bridge",
            CheckSeverity.WARN,
            f"{bridge.name} missing",
            "run sudo maccluster up",
        )
    if desired_ip and not any(str(a) == desired_ip for a in bridge.addresses):
        return DoctorFinding(
            "bridge",
            CheckSeverity.WARN,
            f"{bridge.name} missing IP {desired_ip}",
            f"addrs={list(map(str, bridge.addresses))}",
        )
    return DoctorFinding(
        "bridge",
        CheckSeverity.OK,
        f"{bridge.name} up={bridge.admin_up}",
        f"addrs={list(map(str, bridge.addresses))}",
    )


def check_peers(
    peers: list[tuple[Node, ReachabilityState]],
) -> DoctorFinding:
    if not peers:
        return DoctorFinding("peers", CheckSeverity.INFO, "no peers", "")
    down = [n.id for n, s in peers if s == ReachabilityState.DOWN]
    if down:
        return DoctorFinding(
            "peers",
            CheckSeverity.WARN,
            f"{len(down)} peer(s) unreachable",
            ", ".join(down),
        )
    return DoctorFinding("peers", CheckSeverity.OK, "all peers reachable", "")


def check_mesh(mesh) -> DoctorFinding:
    """Fabric mesh: fully meshed vs partial/isolated (alive ≠ meshed)."""
    from maccluster.domain.enums import MeshVerdict
    from maccluster.domain.models import MeshHealth

    if mesh is None or not isinstance(mesh, MeshHealth):
        return DoctorFinding("mesh", CheckSeverity.INFO, "mesh not assessed", "")
    if mesh.verdict == MeshVerdict.OK:
        return DoctorFinding("mesh", CheckSeverity.OK, mesh.summary, "fully meshed")
    if mesh.verdict == MeshVerdict.SINGLE:
        return DoctorFinding("mesh", CheckSeverity.INFO, mesh.summary, "")
    if mesh.verdict == MeshVerdict.PARTIAL:
        return DoctorFinding(
            "mesh",
            CheckSeverity.WARN,
            mesh.summary,
            "some peers down — self may still be alive (bridge/TB)",
        )
    if mesh.verdict == MeshVerdict.ISOLATED:
        return DoctorFinding(
            "mesh",
            CheckSeverity.WARN,
            mesh.summary,
            "HTTP/process alive on self does not mean cluster is meshed",
        )
    return DoctorFinding("mesh", CheckSeverity.INFO, mesh.summary, "")


def check_rdma(rdma) -> DoctorFinding:
    from maccluster.domain.models import RdmaStatus

    if rdma is None or not isinstance(rdma, RdmaStatus):
        return DoctorFinding("rdma", CheckSeverity.INFO, "rdma not probed", "")
    if not rdma.tool_available:
        return DoctorFinding(
            "rdma",
            CheckSeverity.INFO,
            "rdma_ctl unavailable",
            rdma.detail,
        )
    if rdma.enabled is True:
        return DoctorFinding("rdma", CheckSeverity.OK, "RDMA enabled", rdma.detail)
    if rdma.enabled is False:
        return DoctorFinding(
            "rdma",
            CheckSeverity.INFO,
            "RDMA disabled",
            "enable only in Recovery OS: rdma_ctl enable (not via maccluster)",
        )
    return DoctorFinding("rdma", CheckSeverity.INFO, "RDMA status unknown", rdma.detail)


def check_rdma_device_to_peer(rdma, arep_peers: list[dict]) -> DoctorFinding:
    """WARN when the OS has RDMA on but arep sees no rdma-capable peer.

    *arep_peers* is the ``peers`` list of ``arep status --json``; a peer
    advertises a usable link device via ``transportCapable`` containing
    ``"rdma"``. Nothing here enables RDMA — that is Recovery-OS only.
    """
    from maccluster.domain.models import RdmaStatus

    if not isinstance(rdma, RdmaStatus) or rdma.enabled is not True:
        return DoctorFinding(
            "rdma_device_to_peer",
            CheckSeverity.INFO,
            "rdma peer path not assessed",
            "rdma_ctl not enabled or unknown",
        )
    capable: list[str] = []
    for peer in arep_peers or ():
        if not isinstance(peer, dict):
            continue
        caps = peer.get("transportCapable")
        names = [str(c).lower() for c in caps] if isinstance(caps, list) else []
        if "rdma" in names:
            capable.append(str(peer.get("displayName") or peer.get("fingerprint") or "?"))
    if not capable:
        return DoctorFinding(
            "rdma_no_device_to_peer",
            CheckSeverity.WARN,
            "RDMA enabled but arep reports no rdma-capable peer",
            f"arep peers={len(arep_peers or ())}; check TB link + pairing (arep status --json)",
        )
    return DoctorFinding(
        "rdma_device_to_peer",
        CheckSeverity.INFO,
        f"rdma path to {len(capable)} peer(s): {', '.join(capable)}",
        "",
    )


def check_heal_heartbeat(hb, *, service_installed: bool = False) -> DoctorFinding:
    from maccluster.domain.models import HealHeartbeat

    if hb is None or not isinstance(hb, HealHeartbeat):
        return DoctorFinding("heal_heartbeat", CheckSeverity.INFO, "heartbeat n/a", "")
    if not service_installed and hb.age_seconds is None:
        return DoctorFinding(
            "heal_heartbeat",
            CheckSeverity.INFO,
            "heal service not installed",
            "maccluster service install",
        )
    if hb.stale and service_installed:
        return DoctorFinding(
            "heal_heartbeat",
            CheckSeverity.WARN,
            "heal heartbeat stale",
            hb.detail,
        )
    if hb.stale:
        return DoctorFinding(
            "heal_heartbeat",
            CheckSeverity.INFO,
            "no fresh heal heartbeat",
            hb.detail,
        )
    return DoctorFinding("heal_heartbeat", CheckSeverity.OK, "heal loop fresh", hb.detail)


def check_exo(exo) -> DoctorFinding:
    from maccluster.domain.models import ExoCorrelation

    if exo is None or not isinstance(exo, ExoCorrelation):
        return DoctorFinding("exo", CheckSeverity.SKIPPED, "exo not probed", "pass --exo")
    if not exo.http_ok:
        return DoctorFinding(
            "exo",
            CheckSeverity.INFO,
            "exo not reachable",
            exo.error or exo.summary,
        )
    if exo.mesh_ok is False:
        return DoctorFinding(
            "exo",
            CheckSeverity.WARN,
            "exo http-alive but mesh incomplete/stale",
            exo.summary,
        )
    return DoctorFinding("exo", CheckSeverity.OK, exo.summary, exo.instances_summary or "")


def check_iperf(available: bool) -> DoctorFinding:
    if available:
        return DoctorFinding("iperf3", CheckSeverity.INFO, "iperf3 available", "")
    return DoctorFinding(
        "iperf3",
        CheckSeverity.INFO,
        "iperf3 not found (optional)",
        "brew install iperf3",
    )


def check_arep_bench_history(
    age_days: float | None, *, stale_after_days: float = 7.0
) -> DoctorFinding | None:
    """arep's own bench history: INFO once it is stale, silent when there is none."""
    if age_days is None:
        return None
    if age_days > stale_after_days:
        return DoctorFinding(
            "arep_bench",
            CheckSeverity.INFO,
            f"arep bench history stale ({age_days:.0f}d old)",
            "refresh: arep bench --peer <node> --transport both",
        )
    return DoctorFinding(
        "arep_bench",
        CheckSeverity.OK,
        f"arep bench history fresh ({age_days:.1f}d old)",
        "",
    )


def _host_cid(base: str, node_id: str, *, peer: bool) -> str:
    return f"{base}:{node_id}" if peer else base


def _fmt_gib(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.2f}"


def check_host(snap, *, peer: bool = False) -> DoctorFinding:
    from maccluster.domain.models import HostSnapshot

    if not isinstance(snap, HostSnapshot):
        return DoctorFinding("host", CheckSeverity.INFO, "host not probed", "")
    cid = _host_cid("host", snap.node_id, peer=peer)
    if snap.error:
        return DoctorFinding(
            cid,
            CheckSeverity.WARN,
            f"host {snap.node_id} {snap.error}",
            snap.error,
        )
    if snap.ram_used_gb is None and snap.ram_free_gb is None and snap.load_1m is None:
        return DoctorFinding(cid, CheckSeverity.WARN, "host parse failed", "")
    return DoctorFinding(
        cid,
        CheckSeverity.INFO,
        (
            f"{snap.node_id} ram_used={_fmt_gib(snap.ram_used_gb)} GiB "
            f"ram_free={_fmt_gib(snap.ram_free_gb)} GiB load={_fmt_gib(snap.load_1m)}"
        ),
        "",
    )


def check_disk(snap, *, peer: bool = False) -> DoctorFinding:
    from maccluster.constants import DISK_FREE_WARN_GIB
    from maccluster.domain.models import HostSnapshot

    if not isinstance(snap, HostSnapshot):
        return DoctorFinding("disk", CheckSeverity.INFO, "disk not probed", "")
    cid = _host_cid("disk", snap.node_id, peer=peer)
    if snap.error:
        return DoctorFinding(
            cid, CheckSeverity.WARN, f"host {snap.node_id} {snap.error}", snap.error
        )
    if snap.disk_free_gb is None:
        return DoctorFinding(cid, CheckSeverity.WARN, "disk parse failed", "")
    if snap.disk_free_gb < DISK_FREE_WARN_GIB:
        return DoctorFinding(
            cid,
            CheckSeverity.WARN,
            f"{snap.node_id} disk free {_fmt_gib(snap.disk_free_gb)} GiB < {DISK_FREE_WARN_GIB:g} GiB",
            "",
        )
    return DoctorFinding(
        cid,
        CheckSeverity.OK,
        f"{snap.node_id} disk free {_fmt_gib(snap.disk_free_gb)} GiB",
        "",
    )


def check_thermal(snap, *, peer: bool = False) -> DoctorFinding:
    from maccluster.domain.models import HostSnapshot

    if not isinstance(snap, HostSnapshot):
        return DoctorFinding("thermal", CheckSeverity.INFO, "thermal not probed", "")
    cid = _host_cid("thermal", snap.node_id, peer=peer)
    if snap.error:
        return DoctorFinding(
            cid, CheckSeverity.WARN, f"host {snap.node_id} {snap.error}", snap.error
        )
    limit = snap.cpu_speed_limit_pct
    if limit is None:
        return DoctorFinding(cid, CheckSeverity.INFO, f"{snap.node_id} thermal not reported", "")
    if limit < 100:
        return DoctorFinding(
            cid,
            CheckSeverity.WARN,
            f"{snap.node_id} CPU_Speed_Limit={limit}",
            "",
        )
    return DoctorFinding(cid, CheckSeverity.OK, f"{snap.node_id} CPU_Speed_Limit={limit}", "")


def check_ntp(snap, *, peer: bool = False) -> DoctorFinding:
    from maccluster.constants import NTP_OFFSET_WARN_S
    from maccluster.domain.models import HostSnapshot

    if not isinstance(snap, HostSnapshot):
        return DoctorFinding("ntp", CheckSeverity.SKIPPED, "ntp not probed", "")
    cid = _host_cid("ntp", snap.node_id, peer=peer)
    if snap.error:
        return DoctorFinding(
            cid, CheckSeverity.WARN, f"host {snap.node_id} {snap.error}", snap.error
        )
    if snap.ntp_missing:
        return DoctorFinding(cid, CheckSeverity.SKIPPED, "sntp not found", "")
    if snap.ntp_offset_s is None:
        return DoctorFinding(cid, CheckSeverity.SKIPPED, "ntp not measured", "")
    off = snap.ntp_offset_s
    if abs(off) > NTP_OFFSET_WARN_S:
        return DoctorFinding(
            cid,
            CheckSeverity.WARN,
            f"{snap.node_id} ntp offset {off:.3f}s",
            f"|offset| > {NTP_OFFSET_WARN_S:g}s",
        )
    return DoctorFinding(cid, CheckSeverity.OK, f"{snap.node_id} ntp offset {off:.3f}s", "")


def check_power(snap, *, peer: bool = False) -> DoctorFinding:
    """WARN when a node can doze mid-sync: sleep < 30 min (but not 0) or powernap on.

    The regression class this guards: node-b stood at sleep=1, which parked the
    node daily and broke the cluster. sleep=0 means "never sleeps" and is OK.
    INFO when the node's settings could not be read.
    """
    from maccluster.constants import SLEEP_WARN_MIN_MINUTES
    from maccluster.domain.models import HostSnapshot

    if not isinstance(snap, HostSnapshot):
        return DoctorFinding("power", CheckSeverity.INFO, "power not probed", "")
    cid = _host_cid("power", snap.node_id, peer=peer)
    if snap.error:
        return DoctorFinding(
            cid,
            CheckSeverity.INFO,
            f"{snap.node_id} power settings unreadable",
            snap.error,
        )
    if snap.sleep_minutes is None and snap.powernap_enabled is None:
        return DoctorFinding(
            cid, CheckSeverity.INFO, f"{snap.node_id} power settings not reported", ""
        )
    problems: list[str] = []
    if snap.sleep_minutes is not None and 0 < snap.sleep_minutes < SLEEP_WARN_MIN_MINUTES:
        problems.append(f"sleep={snap.sleep_minutes}m < {SLEEP_WARN_MIN_MINUTES}m")
    if snap.powernap_enabled:
        problems.append("powernap=1")
    sleep_txt = "n/a" if snap.sleep_minutes is None else str(snap.sleep_minutes)
    nap_txt = "n/a" if snap.powernap_enabled is None else str(int(snap.powernap_enabled))
    if problems:
        return DoctorFinding(
            cid,
            CheckSeverity.WARN,
            f"{snap.node_id} {', '.join(problems)}",
            "node can doze mid-sync — on that Mac: sudo pmset -a sleep 0 powernap 0",
        )
    return DoctorFinding(
        cid,
        CheckSeverity.OK,
        f"{snap.node_id} sleep={sleep_txt} powernap={nap_txt}",
        "",
    )


def check_rdma_host(snap, *, peer: bool = False) -> DoctorFinding:
    """Fleet-hop RDMA finding (per node_id). Self RDMA uses check_rdma instead."""
    from maccluster.domain.models import HostSnapshot

    if not isinstance(snap, HostSnapshot):
        return DoctorFinding("rdma", CheckSeverity.INFO, "rdma not probed", "")
    cid = _host_cid("rdma", snap.node_id, peer=peer)
    if snap.error:
        return DoctorFinding(
            cid, CheckSeverity.WARN, f"host {snap.node_id} {snap.error}", snap.error
        )
    if not snap.rdma_tool_available:
        return DoctorFinding(cid, CheckSeverity.INFO, f"{snap.node_id} rdma_ctl unavailable", "")
    if snap.rdma_enabled is True:
        return DoctorFinding(cid, CheckSeverity.OK, f"{snap.node_id} RDMA enabled", "")
    if snap.rdma_enabled is False:
        return DoctorFinding(
            cid,
            CheckSeverity.INFO,
            f"{snap.node_id} RDMA disabled",
            "enable only in Recovery OS: rdma_ctl enable (not via maccluster)",
        )
    return DoctorFinding(cid, CheckSeverity.INFO, f"{snap.node_id} RDMA status unknown", "")
