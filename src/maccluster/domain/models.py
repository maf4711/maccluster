"""Domain models (pure data)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from ipaddress import IPv4Address, IPv4Network
from typing import Any

from maccluster.domain.enums import (
    CheckSeverity,
    HealActionKind,
    LinkState,
    NodeRole,
    OverallHealth,
    ReachabilityState,
)


@dataclass(frozen=True)
class Node:
    id: str
    hostnames: tuple[str, ...]
    ip: IPv4Address
    hw_uuid: str
    ssh_target: str | None = None
    role: NodeRole = NodeRole.UNKNOWN

    def with_role(self, role: NodeRole) -> Node:
        return Node(
            id=self.id,
            hostnames=self.hostnames,
            ip=self.ip,
            hw_uuid=self.hw_uuid,
            ssh_target=self.ssh_target,
            role=role,
        )


@dataclass(frozen=True)
class ClusterConfig:
    schema_version: int
    name: str
    subnet: IPv4Network
    bridge_interface: str
    nodes: tuple[Node, ...]
    heal_interval_seconds: int = 30
    ssh_probes_enabled: bool = False
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass(frozen=True)
class ThunderboltPort:
    receptacle_id: str
    interface_name: str | None
    capable: bool
    thunderbolt_version: str | None
    link_speed_gbps: float | None
    link_state: LinkState
    domain_uuid: str | None = None
    peer_name: str | None = None
    bus_uid: str | None = None
    status_raw: str | None = None


@dataclass(frozen=True)
class ThunderboltSnapshot:
    ports: tuple[ThunderboltPort, ...]
    source: str  # system_profiler | ioreg | merged
    host_model: str | None = None


@dataclass(frozen=True)
class BridgeInterface:
    name: str
    exists: bool
    admin_up: bool
    addresses: tuple[IPv4Address, ...] = ()
    members: tuple[str, ...] = ()


@dataclass(frozen=True)
class NodeHealth:
    node: Node
    reachability: ReachabilityState
    link_state: LinkState = LinkState.UNKNOWN
    link_speed_gbps: float | None = None
    rtt_ms: float | None = None
    notes: str = ""


@dataclass(frozen=True)
class HealthSnapshot:
    timestamp: datetime
    cluster_name: str
    self_node_id: str | None
    nodes: tuple[NodeHealth, ...]
    overall: OverallHealth
    bridge: BridgeInterface | None = None
    tb: ThunderboltSnapshot | None = None


@dataclass(frozen=True)
class TopologyLink:
    local_receptacle: str
    peer_hint: str | None
    domain_uuid: str | None
    link_state: LinkState
    matched_node_id: str | None = None
    speed_gbps: float | None = None


@dataclass(frozen=True)
class Topology:
    links: tuple[TopologyLink, ...]
    unmatched_peers: tuple[str, ...] = ()
    complete: bool = False


@dataclass(frozen=True)
class HealAction:
    kind: HealActionKind
    interface: str
    detail: str
    desired_ip: IPv4Address | None = None


@dataclass(frozen=True)
class DoctorFinding:
    check_id: str
    severity: CheckSeverity
    summary: str
    detail: str = ""


@dataclass(frozen=True)
class DoctorReport:
    findings: tuple[DoctorFinding, ...]
    worst: CheckSeverity
    exit_code: int


@dataclass(frozen=True)
class BenchResult:
    target: str
    mbps: float | None
    success: bool
    message: str


@dataclass(frozen=True)
class ServiceState:
    label: str
    installed: bool
    running: bool
    plist_path: str | None
    interval_seconds: int | None = None
    detail: str = ""


@dataclass(frozen=True)
class HostIdentity:
    hostname: str
    hostnames: tuple[str, ...]  # variants for matching
    hw_uuid: str
    model: str | None = None
    arch: str | None = None


@dataclass(frozen=True)
class PlatformInfo:
    is_macos: bool
    is_arm64: bool
    os_version: str | None = None
    machine: str | None = None


@dataclass
class MutateResult:
    actions: list[HealAction] = field(default_factory=list)
    interface: str = ""
    ip: str = ""
    already_configured: bool = False
    tb_link_present: bool = False
    message: str = ""
    partial: bool = False


@dataclass(frozen=True)
class JsonEnvelope:
    schema_version: int
    command: str
    data: dict[str, Any]
