"""Product-wide defaults and labels."""

from __future__ import annotations

import os

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
TIMEOUT_SYNC = 3600.0  # home ditto archives can be large; override via --timeout

PING_COUNT = 1
STATUS_BUDGET_S = 3.0

# Allowlisted tool basenames for ProcessRunner
ALLOWLIST_BASENAMES = frozenset(
    {
        "system_profiler",
        "ioreg",
        "ifconfig",
        "networksetup",
        "netstat",
        "ping",
        "launchctl",
        "sw_vers",
        "sysctl",
        "scutil",
        "iperf3",
        "ssh",
        "scp",
        "ditto",  # Apple metadata-complete copy (sync home)
        "security",  # macOS Keychain
    }
)

# Keychain (login keychain; syncs via iCloud Keychain if enabled for passwords)
KEYCHAIN_SERVICE_CONFIG = "ai.maccluster.cluster-config"
KEYCHAIN_SERVICE_SSH_USER = "ai.maccluster.ssh.user"
KEYCHAIN_SERVICE_SSH_PASSWORD = "ai.maccluster.ssh.password"
KEYCHAIN_ACCOUNT_DEFAULT = "default"

# Default excludes for `maccluster sync home` (newest-wins; never deletes)
SYNC_HOME_EXCLUDES: tuple[str, ...] = (
    ".Trash/",
    ".cache/",
    "Library/Caches/",
    "Library/Logs/",
    "Library/Developer/Xcode/DerivedData/",
    "Library/Developer/CoreSimulator/",
    "Library/Application Support/MobileSync/",
    "Library/Mail/V*/MailData/Envelope Index*",
    ".npm/_cacache/",
    "**/node_modules/",
    "**/__pycache__/",
    "**/.venv/",
    "**/venv/",
    ".DS_Store",
)

# Traffic sampling (status/monitor TX/RX rates)
TRAFFIC_CACHE_DIR_NAME = "maccluster"
TRAFFIC_CACHE_FILE_NAME = "traffic_sample.json"
TRAFFIC_MIN_DT_S = 0.4
TRAFFIC_MAX_DT_S = 120.0

SEARCH_PATHS = (
    "/usr/sbin",
    "/sbin",
    "/usr/bin",
    "/bin",
)
EXTRA_SEARCH_PATHS = (
    os.path.expanduser("~/.local/bin"),
    "/opt/homebrew/bin",
    "/usr/local/bin",
)

PRODUCT_NAME = "maccluster"
