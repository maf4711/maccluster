"""End-to-end: two directory trees synced through LocalTransport (real rsync)."""
import os
import tempfile
import time
import unittest
from pathlib import Path

from docsync.config import Config
from docsync.session import base_file, run_peer
from docsync.transport import LocalTransport


def touch(p: Path, text: str, mtime: int):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    os.utime(p, (mtime, mtime))


class LocalSyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.a, self.b, self.state = base / "A" / "Documents", base / "B" / "Documents", base / "state"
        self.a.mkdir(parents=True)
        self.b.mkdir(parents=True)
        self.cfg = Config(path=str(self.a), trash=False)
        self.t = LocalTransport(self.b)
        self.now = int(time.time()) - 100

    def tearDown(self):
        self.tmp.cleanup()

    def sync(self, dry=False):
        return run_peer(self.cfg, "b", self.t, dry=dry, state_dir=self.state, log=lambda *a: None)

    def test_first_sync_merges_and_reports(self):
        touch(self.a / "only-a.txt", "A" * 100, self.now)
        touch(self.b / "sub dir/only-b.txt", "B" * 2048, self.now)
        rep = self.sync()
        self.assertTrue(rep.ok, rep.error)
        self.assertTrue(rep.first_sync)
        self.assertEqual((rep.pushed_files, rep.pulled_files), (1, 1))
        self.assertEqual(rep.pushed_bytes, 100)
        self.assertEqual(rep.pulled_bytes, 2048)
        self.assertTrue((self.b / "only-a.txt").exists())
        self.assertTrue((self.a / "sub dir/only-b.txt").exists())
        self.assertTrue(base_file("b", self.state).exists())
        self.assertIn("pushed", rep.render())
        self.assertIn("KB", rep.render())

    def test_dry_run_writes_nothing(self):
        touch(self.a / "x.txt", "x", self.now)
        rep = self.sync(dry=True)
        self.assertTrue(rep.dry)
        self.assertEqual(rep.pushed_files, 1)
        self.assertFalse((self.b / "x.txt").exists())
        self.assertFalse(base_file("b", self.state).exists())

    def test_delete_propagates_after_base(self):
        touch(self.a / "gone.txt", "g", self.now)
        touch(self.a / "stay.txt", "s", self.now)
        self.sync()
        (self.b / "gone.txt").unlink()  # deleted on the peer
        rep = self.sync()
        self.assertEqual(rep.deleted_local, 1)
        self.assertFalse((self.a / "gone.txt").exists())
        self.assertTrue((self.a / "stay.txt").exists())
        (self.a / "stay.txt").unlink()  # deleted here
        rep = self.sync()
        self.assertEqual(rep.deleted_remote, 1)
        self.assertFalse((self.b / "stay.txt").exists())

    def test_edit_after_sync_flows_newest(self):
        touch(self.a / "doc.txt", "v1", self.now)
        self.sync()
        touch(self.b / "doc.txt", "v2-remote", self.now + 10)
        rep = self.sync()
        self.assertEqual(rep.pulled_files, 1)
        self.assertEqual((self.a / "doc.txt").read_text(), "v2-remote")

    def test_conflict_keeps_both_copies_on_both_sides(self):
        touch(self.a / "doc.txt", "v1", self.now)
        self.sync()
        touch(self.a / "doc.txt", "local-edit", self.now + 5)
        touch(self.b / "doc.txt", "remote-edit-newer", self.now + 10)
        rep = self.sync()
        self.assertEqual(len(rep.conflicts), 1)
        path, winner, copy = rep.conflicts[0]
        self.assertEqual(winner, "remote")
        self.assertIn(".conflict-", copy)
        for root in (self.a, self.b):
            self.assertEqual((root / "doc.txt").read_text(), "remote-edit-newer")
            self.assertEqual((root / copy).read_text(), "local-edit")
        self.assertTrue(self.sync().ok)  # converged: next run is a no-op
        self.assertEqual(self.sync().transferred_files, 0)

    def test_cloud_only_files_are_never_deleted_or_pulled(self):
        touch(self.a / "keep.txt", "k", self.now)
        self.sync()  # base now knows keep.txt
        orig = self.t.manifest
        # peer reports keep.txt as evicted to iCloud (size -1 marker) instead of present
        self.t.manifest = lambda ex, dl=None: (dl.add("keep.txt") if dl is not None else None) or {}
        try:
            rep = self.sync()
        finally:
            self.t.manifest = orig
        self.assertTrue(rep.ok, rep.error)
        self.assertEqual(rep.cloud_only_remote, 1)
        self.assertEqual((rep.deleted_local, rep.deleted_remote, rep.pushed_files), (0, 0, 0))
        self.assertTrue((self.a / "keep.txt").exists())
        from docsync import manifest as mf
        self.assertIn("keep.txt", mf.load(base_file("b", self.state)))  # base entry survived

    def test_case_only_difference_is_not_a_deletion(self):
        touch(self.a / "Gesundheit/befunde/x.md", "x", self.now)
        self.sync()  # base knows befunde/x.md
        orig = self.t.manifest
        self.t.manifest = lambda ex, dl=None: {"Gesundheit/Befunde/x.md": (self.now, 1)}
        try:
            rep = self.sync(dry=True)
        finally:
            self.t.manifest = orig
        self.assertTrue(rep.ok, rep.error)
        self.assertEqual((rep.deleted_local, rep.deleted_remote, rep.pushed_files, rep.pulled_files), (0, 0, 0, 0))
        self.assertEqual(rep.unchanged, 1)

    def test_deletion_guard_holds_back_mass_deletes(self):
        for i in range(30):
            touch(self.a / f"f{i}.txt", "x", self.now)
        self.sync()
        for i in range(30):
            (self.b / f"f{i}.txt").unlink()
        rep = self.sync()
        self.assertEqual(rep.deleted_local, 0)
        self.assertEqual(rep.deletes_skipped, 30)
        self.assertTrue(any("deletion guard" in w for w in rep.warnings))
        self.assertTrue((self.a / "f0.txt").exists())
        rep = run_peer(self.cfg, "b", self.t, state_dir=self.state, log=lambda *a: None, force_deletes=True)
        self.assertEqual(rep.deleted_local, 30)
        self.assertFalse((self.a / "f0.txt").exists())

    def test_trash_instead_of_unlink(self):
        home = Path(self.tmp.name) / "A"
        self.cfg.trash = True
        touch(self.a / "t.txt", "t", self.now)
        self.sync()
        (self.b / "t.txt").unlink()
        import docsync.session as s
        orig = s.Path.home
        s.Path.home = staticmethod(lambda: home)
        try:
            self.sync()
        finally:
            s.Path.home = orig
        trashed = list((home / ".Trash").rglob("t.txt"))
        self.assertEqual(len(trashed), 1)


if __name__ == "__main__":
    unittest.main()


class ParallelTests(unittest.TestCase):
    def test_two_peers_in_parallel(self):
        import io
        from docsync.session import run_many
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            a, b, c = base / "A", base / "B", base / "C"
            for x in (a, b, c):
                x.mkdir()
            now = int(time.time()) - 100
            touch(a / "from-a.txt", "a", now)
            touch(b / "from-b.txt", "b", now)
            touch(c / "from-c.txt", "c", now)
            cfg = Config(path=str(a), trash=False)
            reps = run_many(cfg, [("b", LocalTransport(b)), ("c", LocalTransport(c))], parallel=True,
                            state_dir=base / "state", log=lambda *x: None)
            self.assertEqual([r.peer for r in reps], ["b", "c"])
            self.assertTrue(all(r.ok for r in reps), [r.error for r in reps])
            self.assertTrue((b / "from-a.txt").exists() and (c / "from-a.txt").exists())
            self.assertTrue((a / "from-b.txt").exists() and (a / "from-c.txt").exists())


class ReliabilityTests(unittest.TestCase):
    setUp, tearDown, sync = LocalSyncTests.setUp, LocalSyncTests.tearDown, LocalSyncTests.sync

    def test_rsync_retries_twice_then_succeeds(self):
        from docsync import transport
        calls = {"n": 0}
        def flaky(*a, **k):
            calls["n"] += 1
            if calls["n"] <= 2:
                raise transport.TransportError("rsync rc=255: Connection timed out during banner exchange")
            return {"files": 1, "bytes": 10, "sent": 10, "received": 0, "skipped": 0}
        orig = (transport._run_rsync_once, transport._sleep)
        transport._run_rsync_once, transport._sleep = flaky, lambda s: None
        try:
            st = transport._run_rsync("a/", "b/", ["x"], None, False, False)
        finally:
            transport._run_rsync_once, transport._sleep = orig
        self.assertEqual((st["retries"], calls["n"]), (2, 3))

    def test_rsync_gives_up_after_three_retries_and_not_on_other_rcs(self):
        from docsync import transport
        orig = (transport._run_rsync_once, transport._sleep)
        transport._sleep = lambda s: None
        try:
            transport._run_rsync_once = lambda *a, **k: (_ for _ in ()).throw(transport.TransportError("rsync rc=255: x"))
            with self.assertRaises(transport.TransportError):
                transport._run_rsync("a/", "b/", ["x"], None, False, False)
            n = {"c": 0}
            def once(*a, **k):
                n["c"] += 1
                raise transport.TransportError("rsync rc=23: partial")
            transport._run_rsync_once = once
            with self.assertRaises(transport.TransportError):
                transport._run_rsync("a/", "b/", ["x"], None, False, False)
            self.assertEqual(n["c"], 1)
        finally:
            transport._run_rsync_once, transport._sleep = orig

    def test_retries_reach_the_report(self):
        from docsync import transport
        touch(self.a / "r.txt", "r", self.now)
        real = transport._run_rsync_once
        calls = {"n": 0}
        def flaky(*a, **k):
            calls["n"] += 1
            if calls["n"] == 1:
                raise transport.TransportError("rsync rc=30: timeout")
            return real(*a, **k)
        orig = (transport._run_rsync_once, transport._sleep)
        transport._run_rsync_once, transport._sleep = flaky, lambda s: None
        try:
            rep = self.sync()
        finally:
            transport._run_rsync_once, transport._sleep = orig
        self.assertTrue(rep.ok, rep.error)
        self.assertEqual(rep.retries, 1)
        self.assertIn("retries", rep.render())

    def test_lock_wait(self):
        import fcntl
        from docsync import cli
        with tempfile.TemporaryDirectory() as d:
            state = Path(d)
            holder = open(state / "run.lock", "w")
            fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
            t0 = time.monotonic()
            with self.assertRaises(SystemExit) as cm:
                cli._lock(state, wait=1)
            self.assertEqual(cm.exception.code, 3)
            self.assertGreaterEqual(time.monotonic() - t0, 1.0)
            holder.close()  # releases the lock
            fh = cli._lock(state, wait=1)
            fh.close()
