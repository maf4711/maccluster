"""Parser help smoke tests."""

from __future__ import annotations

from maccluster.cli.parser import build_parser


def test_parser_has_commands():
    parser = build_parser()
    help_text = parser.format_help()
    for cmd in (
        "tb",
        "init",
        "config",
        "up",
        "heal",
        "status",
        "monitor",
        "topo",
        "doctor",
        "bench",
        "service",
        "sync",
        "remote-install",
        "ssh-config",
        "speedtest",
    ):
        assert cmd in help_text


def test_parse_status():
    parser = build_parser()
    args = parser.parse_args(["status"])
    assert args.command == "status"


def test_parse_heal_loop():
    parser = build_parser()
    args = parser.parse_args(["heal", "--loop", "--interval", "10"])
    assert args.loop is True
    assert args.interval == 10.0
