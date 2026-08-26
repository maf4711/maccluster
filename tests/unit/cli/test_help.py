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
        "keychain",
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


def test_keychain_account_before_and_after_action():
    """--account must work both before and after the subcommand name."""
    parser = build_parser()
    a = parser.parse_args(["keychain", "--account", "acct1", "show"])
    assert a.keychain_action == "show"
    assert a.account == "acct1"
    b = parser.parse_args(["keychain", "show", "--account", "acct2"])
    assert b.keychain_action == "show"
    assert b.account == "acct2"
    c = parser.parse_args(["keychain", "push", "--account", "acct3", "--ssh-user", "mafoe"])
    assert c.keychain_action == "push"
    assert c.account == "acct3"
    assert c.ssh_user == "mafoe"
    d = parser.parse_args(["keychain", "push-peer", "node-b", "--force", "--user", "mafoe"])
    assert d.keychain_action == "push-peer"
    assert d.peer == "node-b"
    assert d.force is True
    assert d.user == "mafoe"
