"""argparse tree for maccluster."""

from __future__ import annotations

import argparse

from maccluster import __version__
from maccluster.constants import DEFAULT_MONITOR_INTERVAL_S


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="maccluster",
        description=(
            "Thunderbolt cluster CLI for Apple Silicon Mac minis (2–4 nodes). "
            "Heal loop is best-effort, not HA."
        ),
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "--config",
        metavar="PATH",
        default=None,
        help="Path to cluster.toml (overrides MACCLUSTER_CONFIG)",
    )
    parser.add_argument("--json", action="store_true", help="JSON output with schema_version")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose errors")

    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    sub.add_parser("tb", help="Show Thunderbolt hardware info")

    p_init = sub.add_parser(
        "init",
        help="Write cluster.toml (checks macOS Keychain first for shared config)",
    )
    p_init.add_argument("--force", action="store_true", help="Overwrite existing (with backup)")
    p_init.add_argument("--name", default="studio-cluster", help="Cluster name")
    p_init.add_argument(
        "--nodes",
        type=int,
        default=4,
        choices=[2, 3, 4],
        help="Number of node stubs (2–4)",
    )
    p_init.add_argument(
        "--no-keychain",
        action="store_true",
        help="Skip Keychain check/save (local template only)",
    )

    p_cfg = sub.add_parser("config", help="Show or validate config")
    cfg_sub = p_cfg.add_subparsers(dest="config_action", metavar="ACTION")
    cfg_sub.add_parser("show", help="Show config")
    cfg_sub.add_parser("validate", help="Validate config and self-match")

    p_up = sub.add_parser("up", help="Ensure local bridge + fixed Self IP")
    p_up.add_argument("--dry-run", action="store_true", help="Plan only (no mutate)")

    p_heal = sub.add_parser(
        "heal",
        help="Ensure bridge/IP once, or --loop (best-effort, not HA)",
    )
    p_heal.add_argument(
        "--loop",
        action="store_true",
        help="Repeat heal on interval (best-effort)",
    )
    p_heal.add_argument(
        "--interval",
        type=float,
        default=None,
        help="Loop interval seconds (default from config, min 5)",
    )

    sub.add_parser("status", help="Cluster status snapshot")

    p_mon = sub.add_parser("monitor", help="Live status refresh")
    p_mon.add_argument(
        "--interval",
        type=float,
        default=DEFAULT_MONITOR_INTERVAL_S,
        help=f"Refresh interval seconds (default {DEFAULT_MONITOR_INTERVAL_S})",
    )

    sub.add_parser("topo", help="Topology / cable map (no rewiring advice)")
    sub.add_parser("doctor", help="Diagnostics")

    p_bench = sub.add_parser("bench", help="Bandwidth test via iperf3")
    p_bench.add_argument("target", nargs="?", help="Peer IP or node id")
    p_bench.add_argument(
        "--duration",
        type=int,
        default=5,
        help="iperf3 duration seconds (max 60)",
    )

    p_st = sub.add_parser(
        "speedtest",
        help=(
            "TB cable grade (40G ideal / 20G ok) + iperf3 over bridge Self-IP "
            "(run at fleet bring-up)"
        ),
    )
    p_st.add_argument(
        "--peer",
        default=None,
        help="Only test this peer id or IP (default: all peers)",
    )
    p_st.add_argument(
        "--duration",
        type=int,
        default=5,
        help="iperf3 seconds (default 5, max 30)",
    )
    p_st.add_argument(
        "--cable-only",
        action="store_true",
        help="Skip iperf3; only classify link speed / cable path",
    )
    p_st.add_argument(
        "--no-server",
        action="store_true",
        help="Do not try to start remote iperf3 -s via SSH",
    )

    p_svc = sub.add_parser(
        "service",
        help="User LaunchAgents: heal --loop and optional sync home schedule",
    )
    svc_sub = p_svc.add_subparsers(dest="service_action", metavar="ACTION")
    svc_sub.add_parser("install", help="Install heal LaunchAgent")
    svc_sub.add_parser("uninstall", help="Remove heal LaunchAgent")
    svc_sub.add_parser("status", help="Show heal LaunchAgent status")
    p_svc_sync_i = svc_sub.add_parser(
        "sync-install",
        help="Install LaunchAgent for periodic sync home (CCC schedule analogue)",
    )
    p_svc_sync_i.add_argument(
        "--interval",
        type=int,
        default=3600,
        help="Seconds between sync runs (default 3600, min 300)",
    )
    svc_sub.add_parser("sync-uninstall", help="Remove sync home LaunchAgent")
    svc_sub.add_parser("sync-status", help="Show sync home LaunchAgent status")

    p_sync = sub.add_parser(
        "sync",
        help="Sync data with peers over TB/SSH (CCC-inspired Home sync layer)",
    )
    sync_sub = p_sync.add_subparsers(dest="sync_action", metavar="TARGET")
    p_home = sync_sub.add_parser(
        "home",
        help=(
            "Two-way Home sync via Apple ditto (conflict policy default: newer "
            "mtime). Full xattrs/ACLs. No deletes. Needs SSH key login to peers."
        ),
    )
    p_home.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what ditto would transfer (no writes)",
    )
    p_home.add_argument(
        "--compare",
        action="store_true",
        help="CCC Compare: Diff-Report only (counts + sample paths, no transfer)",
    )
    p_home.add_argument(
        "--last",
        action="store_true",
        help="Show last sync run log (no transfer)",
    )
    p_home.add_argument(
        "--peer",
        metavar="ID|IP",
        default=None,
        help="Only sync with this node id or IP (default: all peers)",
    )
    p_home.add_argument(
        "--push-only",
        action="store_true",
        help="Only push local → peer",
    )
    p_home.add_argument(
        "--pull-only",
        action="store_true",
        help="Only pull peer → local",
    )
    p_home.add_argument(
        "--user",
        metavar="NAME",
        default=None,
        help="SSH username on peers (default: local $USER)",
    )
    p_home.add_argument(
        "--home",
        metavar="PATH",
        default=None,
        help="Local home path (default: ~)",
    )
    p_home.add_argument(
        "--remote-home",
        metavar="PATH",
        default=None,
        help="Remote home path (default: same as local home path)",
    )
    p_home.add_argument(
        "--exclude",
        action="append",
        default=[],
        metavar="PATTERN",
        help="Extra exclude pattern (repeatable); defaults already skip Caches/Trash/…",
    )
    p_home.add_argument(
        "--exclude-from",
        metavar="FILE",
        default=None,
        help="Exclude patterns file (default: ~/.config/maccluster/sync-excludes)",
    )
    p_home.add_argument(
        "--preset",
        action="append",
        default=[],
        metavar="NAME",
        help=(
            "Include preset (repeatable/comma): documents,desktop,downloads,"
            "developer,pictures,movies,music,library-app,ssh,config"
        ),
    )
    p_home.add_argument(
        "--include",
        action="append",
        default=[],
        metavar="PATH",
        help="Only sync this path under Home (repeatable), e.g. Documents/",
    )
    p_home.add_argument(
        "--conflict-policy",
        choices=["newer", "larger", "prefer-local", "prefer-remote", "skip-conflict"],
        default="newer",
        help="On both-sides exist: newer (default) | larger | prefer-local | prefer-remote | skip-conflict",
    )
    p_home.add_argument(
        "--safetynet",
        action="store_true",
        help="Before overwrite on pull: backup local file to ~/.maccluster-safetynet/",
    )
    p_home.add_argument(
        "--verify",
        action="store_true",
        help="After pull: sample-check size/mtime of transferred files",
    )
    p_home.add_argument(
        "--verify-sample",
        type=int,
        default=20,
        help="Max files to verify after pull (default 20)",
    )
    p_home.add_argument(
        "--quick",
        action="store_true",
        help="Quick update: prefer local files touched since last successful sync",
    )
    p_home.add_argument(
        "--max-files",
        type=int,
        default=None,
        help="Batch limit: max files this run (remainder next run)",
    )
    p_home.add_argument(
        "--max-bytes",
        type=int,
        default=None,
        help="Batch limit: max payload bytes this run",
    )
    p_home.add_argument(
        "--min-free",
        type=int,
        default=None,
        metavar="BYTES",
        help="Abort if free space on local or peer is below this many bytes",
    )
    p_home.add_argument(
        "--apfs-snapshot",
        action="store_true",
        help="Opt-in: tmutil localsnapshot before transfer (APFS, may need privileges)",
    )
    p_home.add_argument(
        "--notify",
        action="store_true",
        help="macOS Notification Center on failure",
    )
    p_home.add_argument(
        "--no-speedtest",
        action="store_true",
        help="Skip TB cable/speedtest preflight",
    )
    p_home.add_argument(
        "--timeout",
        type=float,
        default=None,
        help="Per-step timeout seconds (default 3600)",
    )
    p_home.add_argument(
        "--no-progress",
        action="store_true",
        help="Disable live progress bar (percent / path / speed)",
    )

    p_ri = sub.add_parser(
        "remote-install",
        help=(
            "Install MacCluster on a peer over the TB bridge only "
            "(BindAddress Self-IP → peer 10.42.0.x; not Wi‑Fi)"
        ),
    )
    p_ri.add_argument(
        "peer",
        help="Peer node id or cluster IP (e.g. node-b or 10.42.0.2)",
    )
    p_ri.add_argument("--user", default=None, help="SSH user (default $USER)")
    p_ri.add_argument(
        "--no-config",
        action="store_true",
        help="Do not copy local cluster.toml to peer",
    )
    p_ri.add_argument(
        "--no-ssh-config",
        action="store_true",
        help="Skip writing ~/.ssh/config.d/maccluster",
    )
    p_ri.add_argument("--dry-run", action="store_true", help="Plan only")
    p_ri.add_argument(
        "--timeout",
        type=float,
        default=600.0,
        help="SSH/SCP timeout seconds (default 600)",
    )

    p_sshc = sub.add_parser(
        "ssh-config",
        help="Write OpenSSH config so 10.42.0.* uses BindAddress on TB bridge only",
    )
    p_sshc.add_argument("--user", default=None, help="SSH user (default $USER)")

    # Shared flags for keychain parent + every subcommand so both
    # `keychain --account X show` and `keychain show --account X` work.
    # SUPPRESS: subparser must not overwrite parent value with default None.
    kc_common = argparse.ArgumentParser(add_help=False)
    kc_common.add_argument(
        "--account",
        default=argparse.SUPPRESS,
        help="Keychain account label (default: default)",
    )

    p_kc = sub.add_parser(
        "keychain",
        parents=[kc_common],
        help=(
            "Local macOS Keychain store for cluster.toml + SSH user/password "
            "(this Mac only; use push-peer / remote-install for peers)"
        ),
    )
    kc_sub = p_kc.add_subparsers(dest="keychain_action", metavar="ACTION")
    kc_sub.add_parser(
        "show",
        parents=[kc_common],
        help="Show what is stored (password never printed)",
    )
    p_push = kc_sub.add_parser(
        "push",
        parents=[kc_common],
        help="Push local cluster.toml (+SSH user) into this Mac's Keychain",
    )
    p_push.add_argument("--ssh-user", default=None, help="SSH user for peers (e.g. mafoe)")
    p_push.add_argument(
        "--ssh-password",
        default=None,
        help="Optional peer SSH password for bootstrap (stored in Keychain only)",
    )
    p_pull = kc_sub.add_parser(
        "pull",
        parents=[kc_common],
        help="Pull Keychain config → ~/.config/maccluster/cluster.toml",
    )
    p_pull.add_argument("--force", action="store_true", help="Overwrite existing file")
    kc_sub.add_parser(
        "delete",
        parents=[kc_common],
        help="Remove MacCluster items from Keychain",
    )
    p_push_peer = kc_sub.add_parser(
        "push-peer",
        parents=[kc_common],
        help=(
            "Copy cluster.toml to a peer over the TB bridge and try "
            "`keychain push` there (Keychain is local-only; not iCloud)"
        ),
    )
    p_push_peer.add_argument(
        "peer",
        help="Peer node id or cluster IP (e.g. node-b or 10.42.0.2)",
    )
    p_push_peer.add_argument(
        "--user",
        default=None,
        help="SSH user on peer (default: ssh_target user / Keychain / $USER)",
    )
    p_push_peer.add_argument(
        "--force",
        action="store_true",
        help="Overwrite peer cluster.toml if it already exists",
    )
    p_push_peer.add_argument(
        "--no-keychain",
        action="store_true",
        help="Only plant cluster.toml; skip remote keychain push attempt",
    )

    return parser
