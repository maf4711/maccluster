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
    # Always numeric — never a dash (D/U must stay readable)
    assert format_rate(0) == "0 B/s"
    assert "/s" in format_rate(1_000_000)


def test_progress_always_shows_d_u():
    buf = io.StringIO()
    p = SyncProgress(enabled=True, stream=buf, force=True, min_interval_s=0)
    p.phase("inventory", direction="local")
    p.update(files_done=100, bytes_done=50_000, force=True)
    text = buf.getvalue()
    assert "D:" in text
    assert "U:" in text
    # Inventory also reports scan rate
    assert "scan:" in text or "f/s" in text


def test_progress_push_updates_u_lane():
    import time

    buf = io.StringIO()
    p = SyncProgress(enabled=True, stream=buf, force=True, min_interval_s=0)
    p.phase("transfer", direction="push")
    p.update(bytes_done=0, bytes_total=10_000_000, force=True)
    time.sleep(0.25)
    p.update(bytes_done=5_000_000, bytes_total=10_000_000, force=True)
    text = buf.getvalue()
    assert "D:" in text and "U:" in text
    assert "push" in text


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


def test_progress_renders_transport_tag():
    from maccluster.render.progress import NullProgress

    buf = io.StringIO()
    p = SyncProgress(enabled=True, stream=buf, force=True, min_interval_s=0)
    p.phase("transfer", direction="push", detail="node-b", transport="rdma")
    p.update(bytes_done=10, bytes_total=100, force=True)
    text = buf.getvalue()
    assert "transport=rdma" in text
    # tb pass after a downgrade replaces the tag
    p.update(transport="tb", force=True)
    assert "transport=tb" in buf.getvalue()
    NullProgress().phase("transfer", direction="pull", transport="rdma")
    NullProgress().update(transport="rdma")
