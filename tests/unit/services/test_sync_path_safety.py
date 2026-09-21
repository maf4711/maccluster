from pathlib import Path

import pytest

from maccluster.services.sync_inventory import inventory_local, parse_inventory_text
from maccluster.services.sync_paths import contained_path, validate_relpath
from maccluster.services.sync_push import _stage_hardlinks
from maccluster.services.sync_safetynet import backup_before_overwrite


@pytest.mark.parametrize(
    "rel", ["", "/tmp/outside", "../outside", "a/../b", "a//b", "./a", "a\x00b", "a\nb", "a\tb"]
)
def test_reject_unsafe_relative_paths(rel):
    with pytest.raises(ValueError, match="unsafe"):
        validate_relpath(rel)


def test_inventory_rejects_absolute_path():
    with pytest.raises(ValueError, match="absolute"):
        parse_inventory_text("/tmp/outside\t123\t3221225472\n")


def test_staging_and_backup_never_unlink_absolute_source(tmp_path):
    original = tmp_path / "original"
    original.write_text("preserve")
    with pytest.raises(ValueError):
        _stage_hardlinks(
            tmp_path / "home",
            [str(original)],
            tmp_path / "stage",
            abs_ditto="ditto",
            runner=None,
            timeout=1,
        )
    with pytest.raises(ValueError):
        backup_before_overwrite(tmp_path / "home", [str(original)], run_dir=tmp_path / "backups")
    assert original.read_text() == "preserve"


def test_reject_parent_symlink_escape(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (home / "escape").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        contained_path(home, "escape/outside")
    assert contained_path(home, "escape") == home / "escape"


def test_safetynet_preserves_bytes_after_in_place_overwrite(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    source = home / "file"
    source.write_text("before")
    run = tmp_path / "backup"
    assert backup_before_overwrite(home, ["file"], run_dir=run) == 1
    source.write_text("after")
    assert (run / "file").read_text() == "before"
    assert (run / "file").stat().st_ino != source.stat().st_ino


def test_safetynet_preserves_symlinks(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (home / "link").symlink_to("missing")
    run = tmp_path / "backup"
    assert backup_before_overwrite(home, ["link"], run_dir=run) == 1
    assert (run / "link").readlink() == Path("missing")


def test_fast_inventory_emits_directory_symlinks_without_traversal(tmp_path):
    dev = tmp_path / "Developer"
    dev.mkdir()
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / "secret").write_text("not in scope")
    (dev / "link").symlink_to(outside, target_is_directory=True)
    inv = inventory_local(tmp_path, (), ("Developer",))
    assert "Developer/link" in inv
    assert "Developer/link/secret" not in inv
    assert not inv.partial


def test_inventory_explicit_file_include(tmp_path):
    (tmp_path / "note.txt").write_text("keep")
    assert "note.txt" in inventory_local(tmp_path, (), ("note.txt",))


@pytest.mark.parametrize("include", ["note.txt", "link"])
def test_remote_and_local_explicit_file_includes_agree(tmp_path, include):
    import subprocess
    import sys

    from maccluster.services.sync_inventory_remote import _REMOTE_INVENTORY_PY

    (tmp_path / "note.txt").write_text("keep")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "hidden").write_text("not in scope")
    (tmp_path / "link").symlink_to(outside, target_is_directory=True)
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            _REMOTE_INVENTORY_PY,
            str(tmp_path),
            str(tmp_path / "no-excludes"),
            include,
        ],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "skip-hang" not in result.stderr
    assert set(parse_inventory_text(result.stdout)) == {include}
    assert set(inventory_local(tmp_path, (), (include,))) == {include}
