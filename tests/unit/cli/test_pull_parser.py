"""Parser + dispatch coverage for maccluster pull."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from maccluster.cli.parser import build_parser
from maccluster.constants import SYNC_PULL_DEFAULT_PRESETS


def test_parser_has_pull():
    help_text = build_parser().format_help()
    assert "pull" in help_text


def test_parse_pull_defaults():
    parser = build_parser()
    args = parser.parse_args(["pull"])
    assert args.command == "pull"
    assert args.dry_run is False
    assert args.pull_only is False
    assert args.push_only is False
    assert args.full_home is False
    assert args.preset == []
    assert args.conflict_policy == "newer"


def test_parse_pull_flags():
    parser = build_parser()
    args = parser.parse_args(
        [
            "pull",
            "--dry-run",
            "--pull-only",
            "--peer",
            "node-b",
            "--user",
            "a321",
            "--safetynet",
            "--verify",
            "--notify",
            "--no-speedtest",
            "--full-home",
        ]
    )
    assert args.command == "pull"
    assert args.dry_run is True
    assert args.pull_only is True
    assert args.peer == "node-b"
    assert args.user == "a321"
    assert args.safetynet is True
    assert args.verify is True
    assert args.notify is True
    assert args.no_speedtest is True
    assert args.full_home is True


def test_pull_cmd_uses_default_presets():
    from maccluster.commands import pull_cmd

    ctx = MagicMock()
    ctx.json_mode = True
    args = build_parser().parse_args(["pull", "--dry-run", "--peer", "node-b"])

    with patch("maccluster.commands.home_dev_transfer.sync_cmd.run", return_value=0) as mock_run:
        code = pull_cmd.run(ctx, args)

    assert code == 0
    sync_args = mock_run.call_args[0][1]
    assert sync_args.sync_action == "home"
    assert tuple(sync_args.preset) == SYNC_PULL_DEFAULT_PRESETS
    assert sync_args.dry_run is True
    assert sync_args.peer == "node-b"
    assert sync_args.push_only is False
    assert sync_args.pull_only is False


def test_pull_cmd_full_home_clears_presets():
    from maccluster.commands import pull_cmd

    ctx = MagicMock()
    ctx.json_mode = True
    args = build_parser().parse_args(["pull", "--full-home"])

    with patch("maccluster.commands.home_dev_transfer.sync_cmd.run", return_value=0) as mock_run:
        pull_cmd.run(ctx, args)

    sync_args = mock_run.call_args[0][1]
    assert sync_args.preset == []


def test_pull_cmd_custom_preset_override():
    from maccluster.commands import pull_cmd

    ctx = MagicMock()
    ctx.json_mode = True
    args = build_parser().parse_args(["pull", "--preset", "developer"])

    with patch("maccluster.commands.home_dev_transfer.sync_cmd.run", return_value=0) as mock_run:
        pull_cmd.run(ctx, args)

    sync_args = mock_run.call_args[0][1]
    assert sync_args.preset == ["developer"]
