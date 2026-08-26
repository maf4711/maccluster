"""Progress bar helpers."""

from __future__ import annotations

import io

from maccluster.render.progress import (
    SyncProgress,
    format_bytes,
    format_eta,
    format_rate,
    render_bar,
    shorten_path,
)


def test_format_bytes_and_rate():
    assert format_bytes(500) == "500 B"
    assert "KB" in format_bytes(12_000)
    assert "MB" in format_bytes(3_500_000)
    assert format_rate(0) == "— B/s"
    assert "/s" in format_rate(1_000_000)


def test_render_bar_bounds():
    assert render_bar(0, width=10) == "░" * 10
    assert render_bar(100, width=10) == "█" * 10
    assert len(render_bar(50, width=10)) == 10


def test_format_eta():
    assert format_eta(None) == "ETA —"
    assert "s" in format_eta(12)
    assert "m" in format_eta(90)


def test_shorten_path():
    long = "Documents/" + ("x" * 80) + "/file.txt"
    s = shorten_path(long, 30)
    assert len(s) <= 30
    assert "…" in s


def test_progress_draws_percent():
    buf = io.StringIO()
    p = SyncProgress(enabled=True, stream=buf, force=True, min_interval_s=0)
    p.set_totals(files=10, bytes_=1000)
    p.phase("transfer", direction="push")
    p.update(
        path="Documents/a.txt",
        bytes_done=250,
        bytes_total=1000,
        file_index=3,
        file_total=10,
        force=True,
    )
    text = buf.getvalue()
    assert "25.0%" in text or "25%" in text
    assert "push" in text
    assert "Documents/a.txt" in text or "a.txt" in text
    p.finish("done")
    assert "done" in buf.getvalue()
