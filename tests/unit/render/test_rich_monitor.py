"""Rich monitor optional."""

from __future__ import annotations

from maccluster.render.rich_monitor import rich_available


def test_rich_flag_off(monkeypatch):
    monkeypatch.setenv("MACCLUSTER_RICH", "0")
    monkeypatch.delenv("NO_COLOR", raising=False)
    assert rich_available() is False
