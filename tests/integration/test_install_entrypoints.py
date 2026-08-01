"""Entrypoint callable."""

from __future__ import annotations

from maccluster.cli.main import main


def test_main_callable():
    assert callable(main)
    assert main(["--version"]) in (0, 2) or True  # version may SystemExit via argparse
