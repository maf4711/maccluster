"""Parser coverage for sync home."""

from __future__ import annotations

import pytest

from maccluster.cli.parser import build_parser


def test_parser_has_sync():
    help_text = build_parser().format_help()
    assert "sync" in help_text


def test_parse_sync_home_flags():
    parser = build_parser()
    args = parser.parse_args(
        [
            "sync",
            "home",
            "--dry-run",
            "--peer",
            "node-b",
            "--push-only",
            "--user",
            "a321",
            "--exclude",
            "Movies/",
            "--timeout",
            "120",
            "--no-progress",
        ]
    )
    assert args.command == "sync"
    assert args.sync_action == "home"
    assert args.dry_run is True
    assert args.peer == "node-b"
    assert args.push_only is True
    assert args.user == "a321"
    assert args.exclude == ["Movies/"]
    assert args.timeout == 120.0
    assert args.no_progress is True


def test_parse_sync_dev_flags():
    parser = build_parser()
    args = parser.parse_args(
        [
            "sync",
            "dev",
            "--dry-run",
            "--compare",
            "--peer",
            "node-b",
            "--push-only",
            "--no-speedtest",
            "--no-progress",
        ]
    )
    assert args.command == "sync"
    assert args.sync_action == "dev"
    assert args.dry_run is True
    assert args.compare is True
    assert args.peer == "node-b"
    assert args.push_only is True
    assert args.no_speedtest is True
    assert args.wifi_top == 10
    assert args.no_wifi is False
    assert args.wifi_only is False
    assert args.no_mcprt is False
    assert args.no_testflight is False


def test_parse_sync_dev_mcprt_flags():
    parser = build_parser()
    skip = parser.parse_args(["sync", "dev", "--no-mcprt", "--no-testflight"])
    assert skip.no_mcprt is True
    assert skip.no_testflight is True


def test_parse_sync_home_has_no_mcprt_flags():
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["sync", "home", "--no-mcprt"])


def test_parse_sync_dev_wifi_flags():
    parser = build_parser()
    no_wifi = parser.parse_args(["sync", "dev", "--no-wifi"])
    assert no_wifi.no_wifi is True
    assert no_wifi.wifi_only is False
    wifi_only = parser.parse_args(["sync", "dev", "--wifi-only", "--wifi-top", "3"])
    assert wifi_only.wifi_only is True
    assert wifi_only.wifi_top == 3


def test_parse_sync_dev_wifi_flags_mutex():
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["sync", "dev", "--no-wifi", "--wifi-only"])


def test_parse_sync_home_has_no_wifi_flags():
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["sync", "home", "--wifi-only"])
    home = parser.parse_args(["sync", "home", "--dry-run"])
    assert not hasattr(home, "wifi_top")


def test_parse_sync_developer_alias():
    parser = build_parser()
    args = parser.parse_args(["sync", "developer", "--last"])
    assert args.sync_action in ("dev", "developer")
    assert args.last is True


def test_sync_help_lists_dev_target():
    parser = build_parser()
    help_text = parser.format_help()
    assert "sync" in help_text
    # subparser names appear in the top-level usage or command list
    sync_help = None
    for action in parser._subparsers._group_actions:  # noqa: SLF001
        for name, sub in action.choices.items():
            if name == "sync":
                sync_help = sub.format_help()
                break
    assert sync_help is not None
    assert "dev" in sync_help
    assert "home" in sync_help


def test_sync_bare_requires_action_in_main(capsys):
    from maccluster.cli.main import main

    assert main(["sync"]) == 2
    err = capsys.readouterr().err
    assert "home" in err
    assert "dev" in err


def test_parse_identical_and_force_icloud():
    parser = build_parser()
    args = parser.parse_args(
        [
            "sync",
            "home",
            "--identical",
            "--force-icloud",
            "--icloud-timeout",
            "15",
            "--icloud-max-seconds",
            "120",
            "--peer",
            "node-b",
            "--no-speedtest",
        ]
    )
    assert args.identical is True
    assert args.force_icloud is True
    assert args.icloud_timeout == 15.0
    assert args.icloud_max_seconds == 120.0
    assert args.peer == "node-b"


def test_parse_full_home():
    parser = build_parser()
    args = parser.parse_args(["sync", "home", "--full-home", "--no-speedtest"])
    assert args.full_home is True
