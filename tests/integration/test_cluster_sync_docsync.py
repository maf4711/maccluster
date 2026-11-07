"""Cluster sync through the real docsync engine against a local-directory peer:
deletions propagate, a case-only rename is no deletion, the deletion guard holds."""

from __future__ import annotations

import os
import time
from ipaddress import IPv4Address
from pathlib import Path

import pytest

docsync_api = pytest.importorskip("docsync.api")

from maccluster.domain.models import Node  # noqa: E402
from maccluster.services import sync_docsync as sd  # noqa: E402


class _Prog:
    def __init__(self):
        self.notes: list[str] = []

    def note(self, text: str) -> None:
        self.notes.append(text)


class LocalPeerApi:
    """The real docsync api; only peer resolution points at a directory on this machine."""

    def __init__(self, peer_home: Path):
        self.peer_home = peer_home
        self.SyncRequest, self.RootSpec, self.PeerSpec = (
            docsync_api.SyncRequest,
            docsync_api.RootSpec,
            docsync_api.PeerSpec,
        )

    def resolve_peer(self, hosts, user="", name="peer", log=None):
        return "/", "LAN"  # "this machine": docsync uses the root's peer path

    def sync(self, req):
        return docsync_api.sync(req)


def _touch(p: Path, text: str, mtime: float) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    os.utime(p, (mtime, mtime))


@pytest.fixture
def homes(tmp_path: Path):
    a, b = tmp_path / "home-a", tmp_path / "home-b"
    (a / "Documents").mkdir(parents=True)
    (b / "Documents").mkdir(parents=True)
    return a, b, tmp_path / "docsync-state"


def _sync(a: Path, b: Path, state: Path, *, force_deletes: bool = False):
    peers = [
        (
            Node(id="test-peer", hostnames=("t.local",), ip=IPv4Address("10.99.0.2"), hw_uuid="x"),
            "u@t.local",
        )
    ]
    return sd.run(
        prog=_Prog(),
        local_home=a,
        remote_home=str(b),
        peers=peers,
        self_ip="",
        via="wifi",
        target="home",
        includes=("Documents/",),
        excludes=(),
        conflict_policy="newer",
        dry_run=False,
        compare_only=False,
        default_user="u",
        force_deletes=force_deletes,
        api=LocalPeerApi(b),
        state_dir=state,
    )


def test_delete_on_peer_propagates(homes):
    a, b, state = homes
    now = time.time() - 100
    _touch(a / "Documents" / "keep.txt", "k", now)
    _touch(a / "Documents" / "gone.txt", "g", now)
    first = _sync(a, b, state)
    assert first.peers[0].ok and (b / "Documents" / "gone.txt").exists()
    (b / "Documents" / "gone.txt").unlink()
    second = _sync(a, b, state)
    assert second.peers[0].ok, second.peers[0].message
    assert not (a / "Documents" / "gone.txt").exists()
    assert (a / "Documents" / "keep.txt").exists()
    assert "deleted 1" in second.peers[0].message


def test_case_only_rename_is_not_a_deletion(homes):
    a, b, state = homes
    now = time.time() - 100
    _touch(a / "Documents" / "befunde" / "x.md", "x", now)
    _sync(a, b, state)
    (b / "Documents" / "befunde").rename(b / "Documents" / "Befunde")
    res = _sync(a, b, state)
    assert res.peers[0].ok
    assert "deleted 0" in res.peers[0].message
    assert (a / "Documents" / "befunde" / "x.md").exists()


def test_deletion_guard_and_force(homes):
    a, b, state = homes
    now = time.time() - 100
    for i in range(30):
        _touch(a / "Documents" / f"f{i}.txt", "x", now)
    _sync(a, b, state)
    for i in range(30):
        (b / "Documents" / f"f{i}.txt").unlink()
    held = _sync(a, b, state)
    assert "30 deletions held back" in held.peers[0].message
    assert (a / "Documents" / "f0.txt").exists()
    forced = _sync(a, b, state, force_deletes=True)
    assert "deleted 30" in forced.peers[0].message
    assert not (a / "Documents" / "f0.txt").exists()
