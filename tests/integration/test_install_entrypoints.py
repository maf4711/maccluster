"""Entrypoint callable."""

from __future__ import annotations

from maccluster.cli.main import main


def test_main_version(capsys):
    from maccluster import __version__

    assert main(["--version"]) == 0
    assert capsys.readouterr().out.strip() == f"maccluster {__version__}"
