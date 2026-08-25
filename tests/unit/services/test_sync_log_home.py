"""Sync run logs belong to the home being synced, not the caller's real home."""

from __future__ import annotations

from pathlib import Path

from maccluster.config.paths import default_sync_log_dir
from maccluster.domain.models import SyncHomeResult
from maccluster.services.sync_history import write_run_log


def test_default_sync_log_dir_follows_given_home(tmp_path: Path):
    assert default_sync_log_dir(tmp_path) == tmp_path / "Library" / "Logs" / "maccluster"


def test_write_run_log_dev_target_does_not_create_library_under_tree(tmp_path: Path):
    """sync dev must not plant Library/Logs inside ~/Developer."""
    from maccluster.services.sync_history import _home_of

    tree = tmp_path / "Developer"
    tree.mkdir()
    result = SyncHomeResult(
        local_home=str(tree),
        dry_run=True,
        strategy="newer",
        peers=(),
        excludes=(),
        target="dev",
    )
    assert _home_of(result) == Path.home()
    path = write_run_log(result, log_dir=tmp_path / "logs")
    assert path.is_file()
    assert not (tree / "Library").exists()


def test_write_run_log_uses_synced_home_not_real_home(tmp_path: Path):
    """A sync of a throwaway home must not write into the user's real
    ~/Library/Logs/maccluster (test runs were polluting real history)."""
    result = SyncHomeResult(
        local_home=str(tmp_path),
        dry_run=True,
        strategy="newer",
        peers=(),
        excludes=(),
    )
    path = write_run_log(result)
    assert Path(path).is_relative_to(tmp_path)
    assert not Path(path).is_relative_to(Path.home() / "Library" / "Logs")
