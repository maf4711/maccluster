"""precise_delta — inventory compare with byte-accurate buckets."""

from __future__ import annotations

from maccluster.services.sync_service import FileMeta, precise_delta


def test_precise_delta_only_local_and_remote():
    local = {
        "a.txt": FileMeta(mtime_ns=100, size=10),
        "shared.txt": FileMeta(mtime_ns=50, size=5),
    }
    remote = {
        "b.txt": FileMeta(mtime_ns=100, size=20),
        "shared.txt": FileMeta(mtime_ns=50, size=5),
    }
    d = precise_delta(local, remote, policy="newer")
    assert d.only_local.count == 1
    assert d.only_local.bytes == 10
    assert d.only_remote.count == 1
    assert d.only_remote.bytes == 20
    assert d.equal.count == 1
    assert d.to_push == ("a.txt",)
    assert d.to_pull == ("b.txt",)
    assert d.push_bytes == 10
    assert d.pull_bytes == 20
    assert d.delta_files == 2
    assert d.delta_bytes == 30
    assert not d.in_sync


def test_precise_delta_newer_wins_bytes():
    local = {"x.bin": FileMeta(mtime_ns=200, size=1000)}
    remote = {"x.bin": FileMeta(mtime_ns=100, size=50)}
    d = precise_delta(local, remote, policy="newer")
    assert d.local_newer.count == 1
    assert d.local_newer.bytes == 1000
    assert d.to_push == ("x.bin",)
    assert d.push_bytes == 1000
    assert d.to_pull == ()
    assert d.pull_bytes == 0


def test_precise_delta_in_sync():
    inv = {"same.txt": FileMeta(mtime_ns=1, size=9)}
    d = precise_delta(inv, inv, policy="newer")
    assert d.in_sync
    assert d.delta_files == 0
    assert d.delta_bytes == 0
    assert d.equal.count == 1


def test_precise_delta_skip_conflict():
    local = {"c.txt": FileMeta(mtime_ns=200, size=1)}
    remote = {"c.txt": FileMeta(mtime_ns=100, size=2)}
    d = precise_delta(local, remote, policy="skip-conflict")
    assert d.conflicts_skipped == 1
    assert d.to_push == ()
    assert d.to_pull == ()
    assert d.in_sync
