"""Offline: no cloud SDK imports (duplicate of unit for integration folder)."""

from __future__ import annotations

import importlib


def test_import_package():
    import maccluster
    import maccluster.cli.main

    assert maccluster.__version__
    assert importlib.util.find_spec("maccluster.adapters.process")
