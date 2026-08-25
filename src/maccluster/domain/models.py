"""Domain models (pure data)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from ipaddress import IPv4Address, IPv4Network
from typing import Any

from maccluster.domain.enums import (
    BenchQuality,
    CheckSeverity,
    HealActionKind,
    LinkState,
    MeshVerdict,
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
    peer_mode: str | None = None  # e.g. "Thunderbolt 3", "USB4" from system_profiler


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
class InterfaceCounters:
    """Cumulative interface counters from netstat (Link row)."""

    name: str
    ipkts: int
    ierrs: int
    ibytes: int
    opkts: int
    oerrs: int
    obytes: int
    coll: int = 0
    t_mono: float = 0.0  # time.monotonic() at sample


@dataclass(frozen=True)
class InterfaceTraffic:
    """Per-interface traffic view: cumulative counters + rates over sample Δt."""

    name: str
    ibytes: int
    obytes: int
    ipkts: int
    opkts: int
    ierrs: int
    oerrs: int
    coll: int = 0
    # Rates (None if no previous sample / Δt out of range)
    rx_bps: float | None = None  # bits/s
    tx_bps: float | None = None
    rx_pps: float | None = None  # packets/s
    tx_pps: float | None = None
    ierrs_delta: int | None = None
    oerrs_delta: int | None = None
    sample_dt_s: float | None = None
    rate_available: bool = False


@dataclass(frozen=True)
class NodeHealth:
    node: Node
    reachability: ReachabilityState
    link_state: LinkState = LinkState.UNKNOWN
    link_speed_gbps: float | None = None
    rtt_ms: float | None = None
    notes: str = ""


@dataclass(frozen=True)
class MeshHealth:
    """Fabric mesh: peer reachability matrix summary (not app-layer exo mesh)."""

    expected_peers: int
    peers_up: int
    peers_down: int
    peers_unknown: int
    fully_meshed: bool
    verdict: MeshVerdict
    summary: str
    # Self fabric "alive" signals
    bridge_ok: bool = False
    tb_links: int = 0


@dataclass(frozen=True)
class RdmaStatus:
    """Read-only OS RDMA state (`rdma_ctl status`). Never enables RDMA."""

    tool_available: bool
    enabled: bool | None  # None if unknown / tool missing
    raw: str = ""
    detail: str = ""


@dataclass(frozen=True)
class HealHeartbeat:
    """Last successful heal-loop tick (for hang detection)."""

    path: str
    age_seconds: float | None
    last_ok: bool | None
    last_exit_code: int | None
    stale: bool
    detail: str = ""


@dataclass(frozen=True)
class ExoCorrelation:
    """Optional correlation with local exo API (:52415). Fabric-neutral."""

    probed: bool
    http_ok: bool
    base_url: str
    topology_nodes: int | None = None
    stale_seconds: float | None = None
    runners: int | None = None
    downloads: int | None = None
    rdma_enabled_nodes: int | None = None
    instances_summary: str = ""
    mesh_ok: bool | None = None  # topology matches expected cluster size when known
    summary: str = ""
    error: str | None = None


@dataclass(frozen=True)
class HealthSnapshot:
    timestamp: datetime
    cluster_name: str
    self_node_id: str | None
    nodes: tuple[NodeHealth, ...]
    overall: OverallHealth
    bridge: BridgeInterface | None = None
    tb: ThunderboltSnapshot | None = None
    traffic: tuple[InterfaceTraffic, ...] = ()
    mesh: MeshHealth | None = None
    rdma: RdmaStatus | None = None
    heal_heartbeat: HealHeartbeat | None = None
    exo: ExoCorrelation | None = None


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
class SyncPeerResult:
    """One peer direction result for home sync."""

    peer_id: str
    peer_ip: str
    ssh_target: str
    push_rc: int
    pull_rc: int
    push_stdout: str = ""
    pull_stdout: str = ""
    push_stderr: str = ""
    pull_stderr: str = ""
    ok: bool = False
    skipped: bool = False
    message: str = ""
    push_files: int = 0
    pull_files: int = 0
    push_bytes: int = 0
    pull_bytes: int = 0
    only_local: int = 0
    only_remote: int = 0
    local_newer: int = 0
    remote_newer: int = 0
    equal: int = 0
    conflicts_skipped: int = 0
    sample_push: tuple[str, ...] = ()
    sample_pull: tuple[str, ...] = ()
    verify_ok: bool | None = None
    verify_checked: int = 0
    verify_mismatches: int = 0
    safetynet_backed_up: int = 0
    free_bytes_local: int | None = None
    free_bytes_remote: int | None = None
    truncated: bool = False  # batch limit hit


@dataclass(frozen=True)
class SyncHomeResult:
    """Aggregate result of `maccluster sync home` / `sync dev`."""

    local_home: str
    dry_run: bool
    strategy: str  # newest-wins / conflict policy label
    peers: tuple[SyncPeerResult, ...]
    excludes: tuple[str, ...] = ()
    includes: tuple[str, ...] = ()
    conflict_policy: str = "newer"
    compare_only: bool = False
    safetynet: bool = False
    verify: bool = False
    quick: bool = False
    log_path: str | None = None
    apfs_snapshot: str | None = None
    max_files: int | None = None
    max_bytes: int | None = None
    target: str = "home"  # home | dev

    @property
    def ok(self) -> bool:
        return all(p.ok or p.skipped for p in self.peers) and bool(self.peers)


@dataclass(frozen=True)
class BenchResult:
    target: str
    mbps: float | None
    success: bool
    message: str
    retransmits: int | None = None
    quality: BenchQuality = BenchQuality.UNKNOWN
    flags: tuple[str, ...] = ()


@dataclass(frozen=True)
class SpeedtestPeerResult:
    peer_id: str
    peer_ip: str
    link_speed_gbps: float | None
    cable_grade: str
    cable_summary: str
    iperf_mbps: float | None
    iperf_ok: bool
    iperf_message: str
    good_enough: bool


@dataclass(frozen=True)
class SpeedtestReport:
    """Cable assessment + optional iperf3 over TB bridge."""

    cable_summary: str
    cable_grade: str
    cable_recommendation: str
    best_link_gbps: float | None
    good_enough: bool
    peers: tuple[SpeedtestPeerResult, ...]
    bind_ip: str | None = None
    duration_s: int = 5


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
