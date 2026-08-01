"""Domain enumerations."""

from __future__ import annotations

from enum import Enum


class NodeRole(str, Enum):
    SELF = "self"
    PEER = "peer"
    UNKNOWN = "unknown"


class ReachabilityState(str, Enum):
    UP = "up"
    DOWN = "down"
    UNKNOWN = "unknown"


class LinkState(str, Enum):
    CONNECTED = "connected"
    UNCONNECTED = "unconnected"
    UNKNOWN = "unknown"


class CheckSeverity(str, Enum):
    OK = "ok"
    INFO = "info"
    WARN = "warn"
    ERROR = "error"
    SKIPPED = "skipped"


class HealActionKind(str, Enum):
    NOOP = "noop"
    ENSURE_BRIDGE = "ensure_bridge"
    ADMIN_UP = "admin_up"
    SET_IP = "set_ip"
    ALREADY_CONFIGURED = "already_configured"


class OverallHealth(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"
