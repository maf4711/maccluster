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
LAUNCH_AGENT_WATCHDOG_LABEL = "com.maccluster.heal-watchdog"
LAUNCH_AGENT_WATCHDOG_PLIST = "com.maccluster.heal-watchdog.plist"
LAUNCH_AGENT_SYNC_LABEL = "com.maccluster.sync-home"
LAUNCH_AGENT_SYNC_PLIST = "com.maccluster.sync-home.plist"
DEFAULT_SYNC_INTERVAL_S = 3600
MIN_SYNC_INTERVAL_S = 300
DEFAULT_WATCHDOG_INTERVAL_S = 60
# Heartbeat older than interval * factor ⇒ heal loop considered hung
HEAL_HEARTBEAT_STALE_FACTOR = 3.0

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
        "tmutil",  # optional APFS local snapshot before pull
        "osascript",  # optional Notification Center on sync fail
        "rdma_ctl",  # macOS RDMA status (read-only; enable is Recovery-OS only)
        "arep",  # autoreplikator peer daemon: status --json / xfer (RDMA rung)
        "git",  # MCPRT preflight (commit / merge / push)
        "gh",  # MCPRT: merge open PR
        "bash",  # TestFlight ship.sh
        "vm_stat",
        "df",
        "uptime",
        "pmset",
        "sntp",
    }
)

# doctor --host thresholds
DISK_FREE_WARN_GIB = 20.0
NTP_OFFSET_WARN_S = 2.0

# Keychain (local login keychain only — security cannot create iCloud-sync items)
KEYCHAIN_SERVICE_CONFIG = "ai.maccluster.cluster-config"
KEYCHAIN_SERVICE_SSH_USER = "ai.maccluster.ssh.user"
KEYCHAIN_SERVICE_SSH_PASSWORD = "ai.maccluster.ssh.password"
KEYCHAIN_ACCOUNT_DEFAULT = "default"

DEVELOPER_DIR_NAME = "Developer"

# Default excludes for `maccluster sync home` (newest-wins; never deletes)
SYNC_HOME_EXCLUDES: tuple[str, ...] = (
    ".Trash/",
    ".cache/",
    "Library/Caches/",
    "Library/Logs/",
    "Library/CloudStorage/",  # OneDrive/Google Drive/iCloud mounts hang os.walk
    "Library/Mobile Documents/",
    "Library/Containers/",
    "Library/Group Containers/",
    "Library/Mail/",
    "Library/Messages/",
    "Library/Developer/Xcode/DerivedData/",
    "Library/Developer/CoreSimulator/",
    "Library/Application Support/MobileSync/",
    "Library/Mail/V*/MailData/Envelope Index*",
    ".npm/_cacache/",
    "**/node_modules/",
    "**/__pycache__/",
    "**/.venv/",
    "**/venv/",
    "**/.git/",
    "**/.next/",
    "**/.vercel/",
    "**/.turbo/",
    "**/Pods/",
    ".DS_Store",
    ".maccluster-safetynet/",  # never re-sync SafetyNet undo tree
    ".orbstack/",
    ".docker/",
    ".grok/sessions/",
)

# Extra excludes when the tree root is ~/Developer (`maccluster sync dev`)
SYNC_DEV_WIFI_TOP = 10  # recent top-level git repos over Wi-Fi (.local)
TIMEOUT_MCPRT_GIT = 120.0
TIMEOUT_MCPRT_TESTFLIGHT = 1800.0

SYNC_DEV_EXCLUDES: tuple[str, ...] = (
    "**/.build/",
    "**/.next/",
    "**/.turbo/",
    "**/.pytest_cache/",
    "**/.ruff_cache/",
    "**/DerivedData/",
)

# When user runs bare `sync home` without --include/--preset/--full-home,
# only walk these roots (full $HOME inventory hangs on Library/CloudStorage).
SYNC_DEFAULT_PRESETS: tuple[str, ...] = (
    "documents",
    "desktop",
    "downloads",
    "developer",
    "ssh",
    "config",
)

# Dir basenames skipped during inventory (in addition to exclude patterns).
# Prevents iCloud/FP/cloud FUSE hangs and multi-hour scans of junk trees.
SYNC_INV_SKIP_DIR_NAMES: frozenset[str] = frozenset(
    {
        "imessage_export",
        "node_modules",
        ".git",
        "DerivedData",
        "__pycache__",
        ".venv",
        "venv",
        ".Trash",
        "Library",  # full-home only; includes under Library/ still walk
        ".npm",
        ".cache",
        ".orbstack",
        ".docker",
        "CloudStorage",
        "Mobile Documents",
        "Containers",
        "Group Containers",
        "CoreSimulator",
        ".grok",
        ".next",
        ".vercel",
        ".turbo",
        ".parcel-cache",
        "dist",
        "build",
        "target",  # rust
        ".build",  # swift
        "Pods",
        "Carthage",
        ".gradle",
        ".mypy_cache",
        ".pytest_cache",
        ".tox",
        ".eggs",
        "Horos Data",  # medical imaging volume
        "DATABASE.noindex",
    }
)

# Inventory time budgets (override via env)
SYNC_INV_MAX_SEC = float(os.environ.get("MACCLUSTER_INV_MAX_SEC", "240"))
SYNC_INV_DIR_SEC = float(os.environ.get("MACCLUSTER_INV_DIR_SEC", "6"))

# CCC-style path presets → include roots under $HOME (comma-separated via --preset)
SYNC_PATH_PRESETS: dict[str, tuple[str, ...]] = {
    "documents": ("Documents/",),
    "desktop": ("Desktop/",),
    "downloads": ("Downloads/",),
    "developer": ("Developer/",),
    "pictures": ("Pictures/",),
    "movies": ("Movies/",),
    "music": ("Music/",),
    "library-app": ("Library/Application Support/",),
    "ssh": (".ssh/",),
    "config": (".config/",),
}

# Default scope for `maccluster pull` — practical Home + ~/Developer
# (full $HOME without filters is huge / iCloud-heavy; use --full-home).
SYNC_PULL_DEFAULT_PRESETS: tuple[str, ...] = (
    "documents",
    "desktop",
    "downloads",
    "developer",
    "ssh",
    "config",
)

# Conflict policies (CCC-inspired; default newer = mtime newest-wins)
SYNC_CONFLICT_POLICIES = frozenset(
    {
        "newer",
        "larger",
        "prefer-local",
        "prefer-remote",
        "skip-conflict",
    }
)

SYNC_EXCLUDE_FILE_NAME = "sync-excludes"
SYNC_SAFETYNET_DIR_NAME = ".maccluster-safetynet"
SYNC_LOG_DIR_PARTS = ("Library", "Logs", "maccluster")
SYNC_CACHE_DIR_NAME = "maccluster"
SYNC_STATE_FILE_NAME = "sync_state.json"
SYNC_VERIFY_SAMPLE_DEFAULT = 20
SYNC_QUICK_SLACK_S = 120  # re-check files touched in last N seconds beyond cache

# Traffic sampling (status/monitor TX/RX rates)
TRAFFIC_CACHE_DIR_NAME = "maccluster"
TRAFFIC_CACHE_FILE_NAME = "traffic_sample.json"
TRAFFIC_MIN_DT_S = 0.4
TRAFFIC_MAX_DT_S = 120.0

HEAL_HEARTBEAT_FILE_NAME = "heal_heartbeat.json"

# Path-quality thresholds for iperf3 over TB bridge (Mbit/s)
BENCH_EXCELLENT_MBPS = 30_000.0  # ~30 Gbit/s — healthy TB5 TCP
BENCH_GOOD_MBPS = 1_000.0
BENCH_MARGINAL_MBPS = 100.0
# Flag mesh paths below this (20G cables stay out of the red)
TB_TCP_FLOOR_MBPS = 15_000.0

# Optional exo local API (correlation only; never required)
EXO_DEFAULT_BASE_URL = "http://127.0.0.1:52415"
EXO_PROBE_TIMEOUT_S = 2.0

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
