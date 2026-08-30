"""Pooled, killable directory listing (one helper process, not one per folder)."""

from __future__ import annotations

import sys
import time
from pathlib import Path

from maccluster.services.sync_scandir import (
    REASON_TIMEOUT,
    ScandirWorker,
)

# argv for a helper that never answers — stands in for a wedged iCloud listing
HANGING_ARGV = [sys.executable, "-c", "import time\nwhile True: time.sleep(60)\n"]


def test_worker_lists_entries(tmp_path: Path) -> None:
    (tmp_path / "sub").mkdir()
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    with ScandirWorker() as w:
        rows = w.listdir(tmp_path)
    assert rows is not None
    by_name = {name: (is_dir, is_file) for name, _p, is_dir, is_file in rows}
    assert by_name["sub"] == (True, False)
    assert by_name["a.txt"] == (False, True)


def test_worker_pays_interpreter_startup_once(tmp_path: Path) -> None:
    """The whole point of the fix: N directories must not cost N interpreters."""
    for i in range(40):
        (tmp_path / f"d{i}").mkdir()
        (tmp_path / f"d{i}" / "f").write_text("x", encoding="utf-8")
    with ScandirWorker() as w:
        for i in range(40):
            assert w.listdir(tmp_path / f"d{i}") is not None
        assert w.starts == 1
        assert w.restarts == 0


def test_worker_keeps_unicode_and_nbsp_names(tmp_path: Path) -> None:
    """The real tree is `Dokumente - CM-...`; NBSP must survive the pipe."""
    weird = tmp_path / "Dokumente – CM-KWFVR7JGW3 (486)"
    weird.mkdir()
    (weird / "ümlaut – fïle.txt").write_text("x", encoding="utf-8")
    with ScandirWorker() as w:
        rows = w.listdir(weird)
    assert rows is not None
    assert [r[0] for r in rows] == ["ümlaut – fïle.txt"]


def test_worker_times_out_and_restarts_on_a_hanging_listing(tmp_path: Path) -> None:
    """A directory that never answers must stay killable, and not wedge the walk."""
    w = ScandirWorker(timeout_s=0.4, argv=HANGING_ARGV)
    try:
        t0 = time.monotonic()
        assert w.listdir(tmp_path) is None
        assert (time.monotonic() - t0) < 5.0
        assert w.last_reason == REASON_TIMEOUT
        assert w.restarts == 1
        # A killed helper is replaced, not left dead: the next call tries again.
        assert w.listdir(tmp_path) is None
        assert w.restarts == 2
        assert w.starts == 2
    finally:
        w.close()


def test_worker_recovers_after_its_helper_dies(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    with ScandirWorker(timeout_s=5.0) as w:
        assert w.listdir(tmp_path) is not None
        w._proc.kill()  # simulate the helper being reaped underneath us
        w._proc.wait(timeout=5)
        rows = w.listdir(tmp_path)
    assert rows is not None
    assert w.starts == 2


def test_worker_reports_unreadable_directory(tmp_path: Path) -> None:
    with ScandirWorker() as w:
        assert w.listdir(tmp_path / "does-not-exist") is None
        assert w.last_reason and w.last_reason != REASON_TIMEOUT
        # an unreadable dir is not a hang — the helper keeps serving
        assert w.restarts == 0
