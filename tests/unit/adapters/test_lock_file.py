"""File lock tests."""

from __future__ import annotations

from pathlib import Path

from maccluster.adapters.lock_file import FileLock


def test_lock_acquire_release(tmp_path: Path):
    lock = FileLock()
    path = tmp_path / "mutate.lock"
    with lock.acquire(path, timeout=2):
        assert path.exists()
    assert not path.exists()
