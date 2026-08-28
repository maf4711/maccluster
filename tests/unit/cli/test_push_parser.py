"""Parser + dispatch coverage for maccluster push."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from maccluster.cli.parser import build_parser
from maccluster.constants import SYNC_PULL_DEFAULT_PRESETS


def test_parser_has_push():
    help_text = build_parser().format_help()
    assert "push" in help_text


def test_parse_push_defaults():
    parser = build_parser()
    args = parser.parse_args(["push"])
    assert args.command == "push"
    assert args.dry_run is False
    assert args.pull_only is False
    assert args.push_only is False  # direction applied in command, not argparse
    assert args.both is False
    assert args.full_home is False
    assert args.preset == []
    assert args.conflict_policy == "newer"


def test_parse_push_flags():
    parser = build_parser()
    args = parser.parse_args(
        [
            "push",
            "--dry-run",
            "--peer",
            "node-b",
            "--user",
            "a321",
            "--both",
            "--notify",
            "--full-home",
        ]
    )
    assert args.command == "push"
    assert args.dry_run is True
    assert args.peer == "node-b"
    assert args.user == "a321"
    assert args.both is True
    assert args.notify is True
    assert args.full_home is True


def test_push_cmd_defaults_to_push_only():
    from maccluster.commands import push_cmd

    ctx = MagicMock()
    ctx.json_mode = True
    args = build_parser().parse_args(["push", "--dry-run", "--peer", "node-b"])

    with patch("maccluster.commands.home_dev_transfer.sync_cmd.run", return_value=0) as mock_run:
        code = push_cmd.run(ctx, args)

    assert code == 0
    sync_args = mock_run.call_args[0][1]
    assert sync_args.sync_action == "home"
    assert tuple(sync_args.preset) == SYNC_PULL_DEFAULT_PRESETS
    assert sync_args.push_only is True
    assert sync_args.pull_only is False
    assert sync_args.dry_run is True
    assert sync_args.peer == "node-b"


def test_push_cmd_both_is_two_way():
    from maccluster.commands import push_cmd

    ctx = MagicMock()
    ctx.json_mode = True
    args = build_parser().parse_args(["push", "--both"])

    with patch("maccluster.commands.home_dev_transfer.sync_cmd.run", return_value=0) as mock_run:
        push_cmd.run(ctx, args)

    sync_args = mock_run.call_args[0][1]
    assert sync_args.push_only is False
    assert sync_args.pull_only is False


def test_push_cmd_full_home_clears_presets():
    from maccluster.commands import push_cmd

    ctx = MagicMock()
    ctx.json_mode = True
    args = build_parser().parse_args(["push", "--full-home"])

    with patch("maccluster.commands.home_dev_transfer.sync_cmd.run", return_value=0) as mock_run:
        push_cmd.run(ctx, args)

    sync_args = mock_run.call_args[0][1]
    assert sync_args.preset == []
    assert sync_args.push_only is True
