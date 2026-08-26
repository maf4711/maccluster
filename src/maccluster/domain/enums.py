"""Domain enumerations."""

from __future__ import annotations

from enum import StrEnum


class NodeRole(StrEnum):
    SELF = "self"
    PEER = "peer"
    UNKNOWN = "unknown"


class ReachabilityState(StrEnum):
    UP = "up"
    DOWN = "down"
    UNKNOWN = "unknown"


class LinkState(StrEnum):
    CONNECTED = "connected"
    UNCONNECTED = "unconnected"
    UNKNOWN = "unknown"


class CheckSeverity(StrEnum):
    OK = "ok"
    INFO = "info"
    WARN = "warn"
    ERROR = "error"
    SKIPPED = "skipped"


class HealActionKind(StrEnum):
    NOOP = "noop"
    ENSURE_BRIDGE = "ensure_bridge"
    ADMIN_UP = "admin_up"
    SET_IP = "set_ip"
    ALREADY_CONFIGURED = "already_configured"


class OverallHealth(StrEnum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"


class MeshVerdict(StrEnum):
    """Fabric mesh membership (alive ≠ fully meshed)."""

    OK = "ok"  # all configured peers reachable
    PARTIAL = "partial"  # some peers down
    ISOLATED = "isolated"  # no peers reachable
    SINGLE = "single"  # config has no peers (1-node / self only)
    UNKNOWN = "unknown"


class BenchQuality(StrEnum):
    EXCELLENT = "excellent"  # TB-class ≥ ~30 Gbit/s
    GOOD = "good"  # ≥ 1 Gbit/s
    MARGINAL = "marginal"  # ≥ 100 Mbit/s
    POOR = "poor"
    UNKNOWN = "unknown"
