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
