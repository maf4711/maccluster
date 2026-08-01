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

    p_init = sub.add_parser("init", help="Write cluster.toml template")
    p_init.add_argument("--force", action="store_true", help="Overwrite existing (with backup)")
    p_init.add_argument("--name", default="studio-cluster", help="Cluster name")
    p_init.add_argument(
        "--nodes",
        type=int,
        default=4,
        choices=[2, 3, 4],
        help="Number of node stubs (2–4)",
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

    p_svc = sub.add_parser("service", help="User LaunchAgent for heal --loop")
    svc_sub = p_svc.add_subparsers(dest="service_action", metavar="ACTION")
    svc_sub.add_parser("install", help="Install LaunchAgent")
    svc_sub.add_parser("uninstall", help="Remove LaunchAgent")
    svc_sub.add_parser("status", help="Show LaunchAgent status")

    return parser
