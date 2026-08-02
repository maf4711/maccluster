"""CCC-inspired sync helpers (filters, history, safetynet, verify)."""

from __future__ import annotations

from pathlib import Path

from maccluster.domain.models import SyncHomeResult, SyncPeerResult
from maccluster.services.sync_filters import (
    filter_inventory,
    load_exclude_file,
    matches_include,
    merge_includes,
)
from maccluster.services.sync_history import format_last_run, write_run_log
from maccluster.services.sync_safetynet import backup_before_overwrite, new_run_dir
from maccluster.services.sync_service import FileMeta
from maccluster.services.sync_verify import verify_local_sample


def test_exclude_from_file(tmp_path: Path):
    f = tmp_path / "ex"
    f.write_text("# c\nMovies/\n\n.cache/\n", encoding="utf-8")
    assert load_exclude_file(f) == ("Movies/", ".cache/")
    assert load_exclude_file(tmp_path / "missing") == ()


def test_include_filter():
    inv = {
        "Documents/a.txt": FileMeta(1, 1),
        "Desktop/b.txt": FileMeta(1, 1),
        "Other/c.txt": FileMeta(1, 1),
    }
    filtered = filter_inventory(inv, ("Documents/", "Desktop/"))
    assert "Documents/a.txt" in filtered and "Desktop/b.txt" in filtered
    assert "Other/c.txt" not in filtered
    assert matches_include("Documents/x", ("Documents/",))
    assert not matches_include("Movies/x", ("Documents/",))
    assert merge_includes(None, None) == ()


def test_safetynet_backup(tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()
    (home / "f.txt").write_text("v1", encoding="utf-8")
    run = new_run_dir(tmp_path / "sn")
    n = backup_before_overwrite(home, ["f.txt"], run_dir=run)
    assert n == 1
    assert (run / "f.txt").read_text(encoding="utf-8") == "v1"


def test_verify_sample(tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()
    p = home / "a.txt"
    p.write_text("hello", encoding="utf-8")
    st = p.lstat()
    expected = {"a.txt": FileMeta(mtime_ns=st.st_mtime_ns, size=st.st_size)}
    ok, checked, mis, _ = verify_local_sample(home, expected, ["a.txt"], sample=5)
    assert ok and checked == 1 and mis == 0
    expected_bad = {"a.txt": FileMeta(mtime_ns=st.st_mtime_ns, size=999)}
    ok2, _, mis2, bad = verify_local_sample(home, expected_bad, ["a.txt"], sample=5)
    assert not ok2 and mis2 == 1 and bad


def test_write_run_log(tmp_path: Path):
    result = SyncHomeResult(
        local_home="/tmp/h",
        dry_run=False,
        strategy="newer (Apple ditto)",
        peers=(
            SyncPeerResult(
                peer_id="node-b",
                peer_ip="10.42.0.2",
                ssh_target="u@10.42.0.2",
                push_rc=0,
                pull_rc=0,
                ok=True,
                message="ok",
                push_files=1,
                pull_files=0,
            ),
        ),
        conflict_policy="newer",
    )
    path = write_run_log(result, log_dir=tmp_path)
    assert path.is_file()
    text = format_last_run(
        {
            "strategy": "newer",
            "dry_run": False,
            "compare_only": False,
            "conflict_policy": "newer",
            "local_home": "/tmp/h",
            "peers": [{"peer_id": "node-b", "peer_ip": "10.42.0.2", "ok": True, "message": "ok"}],
        }
    )
    assert "node-b" in text
