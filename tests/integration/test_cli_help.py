"""CLI help integration."""

from __future__ import annotations

from maccluster.cli.main import main


def test_help_exit_zero():
    assert main(["--help"]) == 0


def test_no_command_usage():
    assert main([]) == 2
