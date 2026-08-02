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

    p_svc = sub.add_parser("service", help="User LaunchAgent for heal --loop")
    svc_sub = p_svc.add_subparsers(dest="service_action", metavar="ACTION")
    svc_sub.add_parser("install", help="Install LaunchAgent")
    svc_sub.add_parser("uninstall", help="Remove LaunchAgent")
    svc_sub.add_parser("status", help="Show LaunchAgent status")

    p_sync = sub.add_parser(
        "sync",
        help="Sync data with peers over TB/SSH (separate from mesh bring-up)",
    )
    sync_sub = p_sync.add_subparsers(dest="sync_action", metavar="TARGET")
    p_home = sync_sub.add_parser(
        "home",
        help=(
            "Two-way Home sync via Apple ditto (newest-wins by mtime, full "
            "xattrs/ACLs). No deletes. Needs SSH key login to peers."
        ),
    )
    p_home.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what ditto would transfer (no writes)",
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
        help="Only push local → peer (still newest-wins by mtime)",
    )
    p_home.add_argument(
        "--pull-only",
        action="store_true",
        help="Only pull peer → local (still newest-wins by mtime)",
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

    p_kc = sub.add_parser(
        "keychain",
        help=(
            "macOS Keychain store for cluster.toml + SSH user/password "
            "(iCloud Keychain → peer can pull on init)"
        ),
    )
    kc_sub = p_kc.add_subparsers(dest="keychain_action", metavar="ACTION")
    kc_sub.add_parser("show", help="Show what is stored (password never printed)")
    p_push = kc_sub.add_parser("push", help="Push local cluster.toml (+SSH user) into Keychain")
    p_push.add_argument("--ssh-user", default=None, help="SSH user for peers (e.g. mafoe)")
    p_push.add_argument(
        "--ssh-password",
        default=None,
        help="Optional peer SSH password for bootstrap (stored in Keychain only)",
    )
    p_pull = kc_sub.add_parser(
        "pull", help="Pull Keychain config → ~/.config/maccluster/cluster.toml"
    )
    p_pull.add_argument("--force", action="store_true", help="Overwrite existing file")
    kc_sub.add_parser("delete", help="Remove MacCluster items from Keychain")
    p_kc.add_argument(
        "--account",
        default=None,
        help="Keychain account label (default: default)",
    )

    return parser
