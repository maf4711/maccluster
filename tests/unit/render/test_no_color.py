"""NO_COLOR / rich guard."""

from __future__ import annotations

from maccluster.render.rich_monitor import rich_available


def test_no_color_disables_rich(monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    assert rich_available() is False
