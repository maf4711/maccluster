"""``inventory_local`` reports how much of the tree it actually saw.

A directory the walk had to skip is named, never dropped in silence, and a
walk that was cut short (time budget, hung directory) is flagged partial so
callers can refuse to diff against it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from maccluster.services import sync_inventory
from maccluster.services.sync_inventory import inventory_local


class _HangingWorker:
    """Stands in for ScandirWorker: every directory listing times out."""

    def __init__(self, *, timeout_s: float = 6.0, argv=None) -> None:
        self.starts = 1
        self.restarts = 0
        self.last_reason = sync_inventory.REASON_TIMEOUT

    def listdir(self, path):
        self.restarts += 1
        self.last_reason = sync_inventory.REASON_TIMEOUT
        return None

    def close(self) -> None:
        return None

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        return None


def test_complete_walk_is_not_partial(tmp_path: Path) -> None:
    (tmp_path / "Documents").mkdir()
    (tmp_path / "Documents" / "a.txt").write_text("a", encoding="utf-8")
    inv = inventory_local(tmp_path, ())
    assert inv["Documents/a.txt"]
    assert inv.partial is False
    assert inv.skipped_dirs == ()


def test_time_budget_marks_inventory_partial(tmp_path: Path) -> None:
    (tmp_path / "Documents").mkdir()
    (tmp_path / "Documents" / "a.txt").write_text("a", encoding="utf-8")
    inv = inventory_local(tmp_path, (), max_sec=-1.0)
    assert inv.partial is True
    assert "budget" in inv.partial_reason


def test_hung_directory_is_reported_and_marks_partial(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "Documents").mkdir()
    (tmp_path / "Documents" / "a.txt").write_text("a", encoding="utf-8")
    monkeypatch.setattr(sync_inventory, "ScandirWorker", _HangingWorker)
    inv = inventory_local(tmp_path, ())
    assert inv.partial is True
    # never silently dropped: the skipped directory is named
    assert inv.skipped_dirs
    assert "timed out" in inv.partial_reason
