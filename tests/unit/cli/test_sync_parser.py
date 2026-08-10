"""Parser coverage for sync home."""

from __future__ import annotations

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


def test_sync_bare_requires_action_in_main():
    from maccluster.cli.main import main

    assert main(["sync"]) == 2


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
