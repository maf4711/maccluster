"""bench --compare parser flag."""

from __future__ import annotations

from maccluster.cli.parser import build_parser


def test_bench_compare_flag():
    parser = build_parser()
    args = parser.parse_args(["bench", "--compare"])
    assert args.command == "bench"
    assert args.compare is True
    assert args.target is None
    assert args.mesh is False


def test_bench_compare_default_off_and_peer_filter():
    parser = build_parser()
    a = parser.parse_args(["bench", "10.42.0.2"])
    assert a.compare is False
    b = parser.parse_args(["bench", "--compare", "--peer", "node-b"])
    assert b.compare is True
    assert b.peer == "node-b"


def test_bench_help_mentions_compare():
    parser = build_parser()
    sub = next(a for a in parser._actions if a.dest == "command")
    help_text = sub.choices["bench"].format_help()
    assert "--compare" in help_text
    assert "regression" in help_text.lower()
