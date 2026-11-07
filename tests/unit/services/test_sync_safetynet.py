"""SafetyNet retention: count, age and size limits; prune on every sync and heal tick."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from maccluster.services import sync_safetynet as sn


def _mk(root: Path, stamp: str, size: int) -> Path:
    d = root / stamp
    (d / "Documents").mkdir(parents=True)
    (d / "Documents" / "f.bin").write_bytes(b"x" * size)
    return d


def _stamp(now: datetime, days_ago: float) -> str:
    return (now - timedelta(days=days_ago)).strftime("%Y%m%dT%H%M%SZ")


def test_keep_limit_removes_oldest(tmp_path: Path) -> None:
    now = datetime(2026, 11, 7, 12, 0, tzinfo=UTC)
    for i in range(8):
        _mk(tmp_path, _stamp(now, i), 10)
    removed = sn.prune_old_runs(tmp_path, keep=5, max_age_days=None, max_bytes=None, now=now)
    assert removed == 3
    assert len(sn._run_dirs(tmp_path)) == 5
    assert (tmp_path / _stamp(now, 0)).exists()
    assert not (tmp_path / _stamp(now, 7)).exists()


def test_age_limit_keeps_newest_even_if_old(tmp_path: Path) -> None:
    now = datetime(2026, 11, 7, 12, 0, tzinfo=UTC)
    old, older = _mk(tmp_path, _stamp(now, 40), 10), _mk(tmp_path, _stamp(now, 60), 10)
    removed = sn.prune_old_runs(tmp_path, keep=5, max_age_days=14, max_bytes=None, now=now)
    assert removed == 1
    assert old.exists() and not older.exists()


def test_size_limit_drops_oldest_until_under_cap(tmp_path: Path) -> None:
    now = datetime(2026, 11, 7, 12, 0, tzinfo=UTC)
    runs = [_mk(tmp_path, _stamp(now, i), 1000) for i in range(4)]  # 4 x 1000 B
    removed = sn.prune_old_runs(tmp_path, keep=5, max_age_days=None, max_bytes=2500, now=now)
    assert removed == 2
    assert runs[0].exists() and runs[1].exists()
    assert not runs[2].exists() and not runs[3].exists()
    n, b = sn.safetynet_usage(tmp_path)
    assert (n, b) == (2, 2000)


def test_size_limit_never_removes_the_newest_run(tmp_path: Path) -> None:
    now = datetime(2026, 11, 7, 12, 0, tzinfo=UTC)
    big = _mk(tmp_path, _stamp(now, 0), 5000)
    assert sn.prune_old_runs(tmp_path, max_bytes=100, max_age_days=None, now=now) == 0
    assert big.exists()


def test_non_run_dirs_untouched(tmp_path: Path) -> None:
    keep_me = tmp_path / "notes"
    keep_me.mkdir()
    assert sn.prune_old_runs(tmp_path, keep=0, max_age_days=0, max_bytes=0) == 0
    assert keep_me.exists()


def test_heal_tick_prunes_by_count(tmp_path: Path, monkeypatch, fake_ctx) -> None:
    from maccluster.services import heal_loop_service

    now = datetime.now(UTC)
    for i in range(7):
        _mk(tmp_path, _stamp(now, i), 10)
    monkeypatch.setattr(sn, "default_safetynet_root", lambda: tmp_path)
    heal_loop_service.run_heal_loop(fake_ctx, interval=0.01, max_iterations=1)
    assert len(sn._run_dirs(tmp_path)) == 5


def test_usage_counts_hardlinked_files_once(tmp_path: Path) -> None:
    import os

    now = datetime.now(UTC)
    a = _mk(tmp_path, _stamp(now, 1), 1000)
    b = tmp_path / _stamp(now, 0) / "Documents"
    b.mkdir(parents=True)
    os.link(a / "Documents" / "f.bin", b / "f.bin")  # same inode in two runs
    assert sn.safetynet_usage(tmp_path) == (2, 1000)
