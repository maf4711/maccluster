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


def check_iperf(available: bool) -> DoctorFinding:
    if available:
        return DoctorFinding("iperf3", CheckSeverity.INFO, "iperf3 available", "")
    return DoctorFinding(
        "iperf3",
        CheckSeverity.INFO,
        "iperf3 not found (optional)",
        "brew install iperf3",
    )
