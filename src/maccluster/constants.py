"""Product-wide defaults and labels."""

from __future__ import annotations

SCHEMA_VERSION = 1
SUPPORTED_SCHEMA_VERSIONS = frozenset({1})

DEFAULT_SUBNET = "10.42.0.0/24"
DEFAULT_BRIDGE = "bridge0"
DEFAULT_HEAL_INTERVAL_S = 30
MIN_HEAL_INTERVAL_S = 5
DEFAULT_MONITOR_INTERVAL_S = 1.5

MIN_NODES = 2
MAX_NODES = 4

CONFIG_DIR_NAME = "maccluster"
CONFIG_FILE_NAME = "cluster.toml"
LOCK_FILE_NAME = "mutate.lock"
LAUNCH_AGENT_LABEL = "com.maccluster.heal"
LAUNCH_AGENT_PLIST = "com.maccluster.heal.plist"

# Interface name: letter first, then alnum/._- up to 16 chars total.
IFACE_NAME_RE = r"^[A-Za-z][A-Za-z0-9_.-]{0,15}$"
NODE_ID_RE = r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$"

CONFIG_MAX_BYTES = 1 * 1024 * 1024  # 1 MiB
AUDIT_MAX_BYTES = 5 * 1024 * 1024

# Process timeouts (seconds)
TIMEOUT_PING = 2.0
TIMEOUT_SSH = 3.0
TIMEOUT_IPERF = 15.0
TIMEOUT_GENERIC = 15.0
TIMEOUT_PROFILER = 30.0

PING_COUNT = 1
STATUS_BUDGET_S = 3.0

# Allowlisted tool basenames for ProcessRunner
ALLOWLIST_BASENAMES = frozenset(
    {
        "system_profiler",
        "ioreg",
        "ifconfig",
        "networksetup",
        "ping",
        "launchctl",
        "sw_vers",
        "sysctl",
        "scutil",
        "iperf3",
        "ssh",
    }
)

SEARCH_PATHS = (
    "/usr/sbin",
    "/sbin",
    "/usr/bin",
    "/bin",
)
EXTRA_SEARCH_PATHS = (
    "/opt/homebrew/bin",
    "/usr/local/bin",
)

PRODUCT_NAME = "maccluster"
