"""`maccluster docsync ...` hands the rest of the line to the bundled docsync CLI."""

from __future__ import annotations

import docsync
import pytest

from maccluster.cli.main import main


def test_docsync_subcommand_runs_bundled_cli(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["docsync", "--version"])
    assert exc.value.code in (0, None)
    assert docsync.__version__ in capsys.readouterr().out


def test_docsync_subcommand_passes_arguments_unchanged(monkeypatch):
    seen = {}
    monkeypatch.setattr("docsync.cli.main", lambda argv: seen.setdefault("argv", argv) and 7)
    assert main(["docsync", "sync", "--all", "--dry-run"]) == 7
    assert seen["argv"] == ["sync", "--all", "--dry-run"]


def test_docsync_is_listed_in_help(capsys):
    assert main(["--help"]) == 0
    assert "docsync" in capsys.readouterr().out
