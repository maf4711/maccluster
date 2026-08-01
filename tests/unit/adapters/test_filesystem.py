"""Filesystem atomic write and symlink policy."""

from __future__ import annotations

from pathlib import Path

import pytest

from maccluster.adapters.filesystem import FileSystem
from maccluster.errors import CliError


def test_atomic_write(tmp_path: Path):
    fs = FileSystem()
    target = tmp_path / "cluster.toml"
    fs.write_text_atomic(target, "hello\n", mode=0o600)
    assert target.read_text() == "hello\n"
    mode = target.stat().st_mode & 0o777
    assert mode == 0o600


def test_backup_on_force(tmp_path: Path):
    fs = FileSystem()
    target = tmp_path / "cluster.toml"
    target.write_text("old\n")
    fs.write_text_atomic(target, "new\n", backup=True)
    assert target.read_text() == "new\n"
    bak = Path(str(target) + ".bak")
    assert bak.exists()
    assert bak.read_text() == "old\n"


def test_refuse_symlink(tmp_path: Path):
    fs = FileSystem()
    real = tmp_path / "real.toml"
    real.write_text("x")
    link = tmp_path / "link.toml"
    link.symlink_to(real)
    with pytest.raises(CliError) as ei:
        fs.write_text_atomic(link, "y\n")
    assert ei.value.exit_code == 2
