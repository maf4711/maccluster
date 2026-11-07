"""File lock tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from maccluster.adapters.lock_file import FileLock
from maccluster.errors import CliError


def test_lock_acquire_release(tmp_path: Path):
    lock = FileLock()
    path = tmp_path / "mutate.lock"
    with lock.acquire(path, timeout=2):
        assert path.exists()
    inode = path.stat().st_ino
    with lock.acquire(path, timeout=0):
        assert path.stat().st_ino == inode


def test_contender_cannot_take_over_empty_lock(tmp_path, monkeypatch):
    import os

    path = tmp_path / "mutate.lock"
    real_write = os.write

    def contend_before_pid_write(fd, data):
        with pytest.raises(CliError, match="lock in progress"):
            with FileLock().acquire(path, timeout=0):
                pytest.fail("both writers acquired the lock")
        return real_write(fd, data)

    monkeypatch.setattr(os, "write", contend_before_pid_write)
    with FileLock().acquire(path, timeout=0):
        assert path.read_text().startswith(str(os.getpid()))


def test_stale_pid_does_not_block_kernel_lock(tmp_path):
    path = tmp_path / "mutate.lock"
    path.write_text("999999999\n0\n")
    with FileLock().acquire(path, timeout=0):
        assert "999999999" not in path.read_text()


def test_lock_rejects_symlink(tmp_path):
    target = tmp_path / "original"
    target.write_text("preserve")
    path = tmp_path / "mutate.lock"
    path.symlink_to(target)
    with pytest.raises(CliError, match="symlink"):
        with FileLock().acquire(path, timeout=0):
            pass
    assert target.read_text() == "preserve"
