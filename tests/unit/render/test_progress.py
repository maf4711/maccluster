"""Progress bar helpers."""

from __future__ import annotations

import io

from maccluster.render.progress import (
    SyncProgress,
    clamp_pct,
    format_bytes,
    format_eta,
    format_rate,
    render_bar,
    render_indeterminate_bar,
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


def test_clamp_pct_never_moves_backwards_within_a_phase():
    """A phase that discovers more work must not rewind the bar (35.0% → 5.1%)."""
    assert clamp_pct(35.0, floor=0.0) == 35.0
    assert clamp_pct(5.1, floor=35.0) == 35.0
    assert clamp_pct(60.0, floor=35.0) == 60.0
    # bounded to 0–100 either way
    assert clamp_pct(-4.0, floor=0.0) == 0.0
    assert clamp_pct(140.0, floor=0.0) == 100.0
    # unknown total stays unknown — an indeterminate phase never fakes a number
    assert clamp_pct(None, floor=35.0) is None


def test_clamp_pct_is_monotonic_over_a_shrinking_denominator():
    floor = 0.0
    seen = []
    # denominator grows as the scan discovers work: raw percent would fall
    for done, total in ((350, 1000), (350, 7000), (900, 7000), (7000, 7000)):
        value = clamp_pct(100.0 * done / total, floor=floor)
        floor = value
        seen.append(value)
    assert seen == sorted(seen)
    assert seen[0] == 35.0 and seen[-1] == 100.0


def test_render_indeterminate_bar_is_fixed_width_and_moves():
    a = render_indeterminate_bar(0.0, width=12)
    b = render_indeterminate_bar(0.5, width=12)
    assert len(a) == len(b) == 12
    assert "█" in a and "█" in b
    assert a != b


def test_progress_percent_never_jumps_backwards_in_a_phase():
    buf = io.StringIO()
    p = SyncProgress(enabled=True, stream=buf, force=True, min_interval_s=0)
    p.phase("inventory", direction="local")
    p.update(files_done=350, files_total=1000, force=True)
    assert "35.0%" in buf.getvalue()
    buf.truncate(0)
    buf.seek(0)
    # the walk finds 6000 more files: raw percent would drop to 5.0%
    p.update(files_done=350, files_total=7000, force=True)
    text = buf.getvalue()
    assert "  5.0%" not in text  # the 5-wide percent field, not a substring of 35.0%
    assert " 35.0%" in text
    # a new phase starts over
    buf.truncate(0)
    buf.seek(0)
    p.phase("transfer", direction="push")
    p.update(bytes_done=10, bytes_total=1000, force=True)
    assert "1.0%" in buf.getvalue()


def test_set_totals_clears_the_scan_counters():
    """Planned bytes are a new unit of work — the scan's bytes are not progress."""
    buf = io.StringIO()
    p = SyncProgress(enabled=True, stream=buf, force=True, min_interval_s=0)
    p.phase("inventory", direction="local")
    p.update(files_done=3000, bytes_done=91_000_000, force=True)
    buf.truncate(0)
    buf.seek(0)
    # plan: 92 MB to move. Without the reset this read ~98% before a byte moved.
    p.set_totals(files=3200, bytes_=92_000_000)
    text = buf.getvalue()
    assert "  0.0%" in text
    assert "0 B/92.00 MB" in text


def test_progress_shows_indeterminate_when_no_total_is_known():
    buf = io.StringIO()
    p = SyncProgress(enabled=True, stream=buf, force=True, min_interval_s=0)
    p.phase("inventory", direction="local")
    p.update(files_done=1234, force=True)
    text = buf.getvalue()
    # no total → no invented percentage
    assert "--%" in text
    assert "1234 files" in text


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
