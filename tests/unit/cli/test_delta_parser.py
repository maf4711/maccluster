"""Parser tests for maccluster delta."""

from __future__ import annotations

from maccluster.cli.parser import build_parser


def test_parse_delta_defaults():
    parser = build_parser()
    args = parser.parse_args(["delta"])
    assert args.command == "delta"
    assert getattr(args, "apply", False) is False
    assert getattr(args, "limit", None) is None
    assert getattr(args, "peer", None) is None


def test_parse_delta_apply_limit_peer():
    parser = build_parser()
    args = parser.parse_args(
        ["delta", "--apply", "--limit", "2", "--peer", "node-b", "--preset", "ssh"]
    )
    assert args.apply is True
    assert args.limit == 2
    assert args.peer == "node-b"
    assert "ssh" in (args.preset or [])


def test_delta_in_top_help():
    help_text = build_parser().format_help()
    assert "delta" in help_text


def test_parse_delta_transport_rung():
    """delta/pull/push must be able to pick a rung: with the TB cable down the
    ladder never reaches its wifi rung here, because inventory/compare runs
    before the transfer stage that consults it."""
    parser = build_parser()
    for command in ("delta", "pull", "push"):
        args = parser.parse_args([command, "--transport", "wifi"])
        assert args.transport == "wifi", command
        assert parser.parse_args([command]).transport is None, command
