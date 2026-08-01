"""A-020: monitor refreshes and exits cleanly when max_iterations hit."""

from __future__ import annotations

from io import StringIO

from maccluster.services.monitor_service import run_monitor


def test_monitor_two_iterations(fake_ctx):
    buf = StringIO()
    code = run_monitor(fake_ctx, interval=0.0, max_iterations=2, out=buf)
    assert code == 0
    text = buf.getvalue()
    assert "cluster:" in text or "schema_version" in text
    # separator between frames
    assert text.count("---") >= 2
    # peer-down still shown; monitor must not crash
    assert "node-b" in text or "DOWN" in text or "down" in text
