"""__version__ must match pyproject — the release process bumps both."""

from __future__ import annotations

import tomllib
from pathlib import Path

import maccluster


def test_dunder_version_matches_pyproject():
    pyproject = Path(__file__).parents[2] / "pyproject.toml"
    with pyproject.open("rb") as fh:
        expected = tomllib.load(fh)["project"]["version"]
    assert maccluster.__version__ == expected
