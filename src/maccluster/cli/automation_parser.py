"""Arguments for manual or scheduled unattended maintenance."""

from __future__ import annotations

import argparse

from maccluster.services.package_update import DEFAULT_SOURCE


def _package_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--sha256",
        dest="expected_sha256",
        default=None,
        help="Expected SHA-256 of a local release wheel supplied with --source",
    )
    parser.add_argument(
        "--source",
        default=None,
        help=f"Wheel/source path or HTTPS archive (default: {DEFAULT_SOURCE})",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=None,
        help="Per-operation timeout in seconds (default: 600)",
    )


def _scope_options(parser: argparse.ArgumentParser) -> None:
    _package_options(parser)
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument("--local-only", action="store_true", help="Maintain this Mac only")
    scope.add_argument(
        "--peer",
        default=None,
        metavar="ID|IP",
        help="Maintain only this peer plus this Mac (default: all configured peers)",
    )
    parser.add_argument(
        "--no-update",
        action="store_true",
        help="Skip package builds/installations; heal existing peers",
    )
    parser.add_argument(
        "--sync",
        action="store_true",
        help="Also sync home files with backups and verification (includes Developer)",
    )


def add_automation_parsers(sub: argparse._SubParsersAction) -> None:
    update = sub.add_parser("update", help="Build and verify a local MacCluster update")
    _package_options(update)
    update.add_argument(
        "--dry-run", action="store_true", help="Show plan without network probes or writes"
    )
    automation = sub.add_parser(
        "automation", help="Automate cluster updates, installations, healing and diagnostics"
    )
    actions = automation.add_subparsers(dest="automation_action", required=True, metavar="ACTION")
    run = actions.add_parser("run", help="Maintain configured cluster once")
    _scope_options(run)
    run.add_argument(
        "--dry-run", action="store_true", help="Show plan without network probes or writes"
    )
    run.add_argument("--saved", action="store_true", help="Use options saved by automation install")
    install = actions.add_parser("install", help="Install a recurring maintenance LaunchAgent")
    _scope_options(install)
    install.add_argument(
        "--interval",
        type=int,
        default=86400,
        help="Seconds between maintenance runs (default: 86400)",
    )
    actions.add_parser("status", help="Show schedule and last maintenance receipt")
    actions.add_parser("uninstall", help="Remove the maintenance schedule")
