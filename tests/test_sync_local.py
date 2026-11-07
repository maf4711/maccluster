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


class SharedScanTests(unittest.TestCase):
    def test_run_many_scans_local_once_and_streams_large_transfers(self):
        from docsync import manifest, session
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            a, b, c = base / "A", base / "B", base / "C"
            for x in (a, b, c):
                x.mkdir()
            now = int(time.time()) - 100
            for i in range(20):  # 20 files × 4 MB = 80 MB > STREAM_MIN_BYTES → streams kick in
                (b / f"big{i}.bin").write_bytes(os.urandom(4 << 20))
                os.utime(b / f"big{i}.bin", (now, now))
            touch(a / "from-a.txt", "a", now)
            calls = {"n": 0}
            real = manifest.scan
            def counting(*x, **k):
                calls["n"] += 1
                return real(*x, **k)
            manifest.scan = counting
            try:
                cfg = Config(path=str(a), trash=False, transfer_streams=4)
                reps = session.run_many(cfg, [("b", LocalTransport(b)), ("c", LocalTransport(c))], parallel=True,
                                        state_dir=base / "state", log=lambda *x: None)
            finally:
                manifest.scan = real
            self.assertTrue(all(r.ok for r in reps), [r.error for r in reps])
            self.assertEqual(calls["n"], 1)  # one shared local walk, no post-apply rescans
            rb = [r for r in reps if r.peer == "b"][0]
            self.assertEqual(rb.pulled_files, 20)
            self.assertEqual(rb.streams, 4)
            self.assertTrue(all((a / f"big{i}.bin").exists() for i in range(20)))
            # base of peer b must know the pulled files (touched-path refresh), and from-a.txt
            mb = manifest.load(session.base_file("b", base / "state"))
            self.assertIn("big0.bin", mb)
            self.assertIn("from-a.txt", mb)
            # peer c's base must NOT contain big*.bin pulled from b (they still have to flow to c next run)
            mc = manifest.load(session.base_file("c", base / "state"))
            self.assertNotIn("big0.bin", mc)
            reps2 = session.run_many(cfg, [("c", LocalTransport(c))], parallel=False, state_dir=base / "state", log=lambda *x: None)
            self.assertEqual(reps2[0].pushed_files, 20)


def _has_tool(name):
    import shutil
    return shutil.which(name) is not None


class RootsFidelitySafetyNetTests(unittest.TestCase):
    setUp, tearDown, sync = LocalSyncTests.setUp, LocalSyncTests.tearDown, LocalSyncTests.sync

    def test_two_roots_two_blocks_two_bases(self):
        from docsync.config import Root
        from docsync.report import RunReport
        from docsync.session import run_roots
        base = Path(self.tmp.name)
        a2, b2 = base / "A" / "Desktop", base / "B" / "Desktop"
        a2.mkdir(); b2.mkdir()
        touch(self.a / "doc.txt", "d", self.now)
        touch(a2 / "desk.txt", "x", self.now)
        cfg = Config(path=str(self.a), trash=False,
                     roots=[Root("documents", str(self.a), legacy=True), Root("desktop", str(a2))])
        peer_dirs = {"documents": self.b, "desktop": b2}
        reps = run_roots(cfg, cfg.effective_roots(), lambda r: [("b", LocalTransport(peer_dirs[r.name]))],
                         state_dir=self.state, log=lambda *x: None)
        self.assertEqual([(r.root, r.peer, r.pushed_files) for r in reps], [("documents", "b", 1), ("desktop", "b", 1)])
        self.assertTrue((b2 / "desk.txt").exists() and (self.b / "doc.txt").exists())
        self.assertTrue(base_file("b", self.state, "documents").exists())
        self.assertTrue(base_file("b", self.state, "desktop").exists())
        run = RunReport(); run.peers += reps
        text = run.render()
        self.assertIn("── b [documents]", text)
        self.assertIn("── b [desktop]", text)
        self.assertIn("2 transferred", text)

    def test_legacy_base_is_migrated(self):
        touch(self.a / "x.txt", "x", self.now)
        self.sync()
        new = base_file("b", self.state)
        new.rename(self.state / "base-b.json")  # pretend 0.9 wrote it
        rep = self.sync()
        self.assertTrue(new.exists())
        self.assertFalse((self.state / "base-b.json").exists())
        self.assertEqual((rep.pushed_files, rep.pulled_files, rep.unchanged), (0, 0, 1))

    @unittest.skipUnless(_has_tool("xattr"), "xattr tool missing")
    def test_xattr_and_acl_survive(self):
        import subprocess
        f = self.a / "tagged.pdf"
        touch(f, "pdf", self.now)
        subprocess.run(["xattr", "-w", "com.example.docsync", "kept", str(f)], check=True)
        acl = subprocess.run(["chmod", "+a", "everyone allow read", str(f)]).returncode == 0
        os.utime(f, (self.now, self.now))
        rep = self.sync()
        self.assertTrue(rep.ok, rep.error)
        dst = self.b / "tagged.pdf"
        self.assertEqual(subprocess.run(["xattr", "-p", "com.example.docsync", str(dst)],
                                        capture_output=True, text=True).stdout.strip(), "kept")
        if acl:
            self.assertIn("everyone allow read", subprocess.run(["ls", "-le", str(dst)], capture_output=True, text=True).stdout)

    def test_overwrite_by_pull_is_backed_up(self):
        touch(self.a / "doc.txt", "old-local", self.now)
        self.sync()
        touch(self.b / "doc.txt", "new-from-peer", self.now + 50)
        rep = self.sync()
        self.assertEqual(rep.pulled_files, 1)
        self.assertEqual(rep.safetynet_files, 1)
        self.assertIn("backed up before overwrite", rep.render())
        saved = list((self.a / ".docsync-safetynet").glob("*/doc.txt"))
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0].read_text(), "old-local")
        self.assertEqual((self.a / "doc.txt").read_text(), "new-from-peer")
        # the backup never shows up in a manifest, so it never syncs
        self.assertFalse((self.b / ".docsync-safetynet").exists())
        self.assertEqual(self.sync().transferred_files, 0)

    def test_overwrite_by_push_is_backed_up_on_the_peer(self):
        touch(self.a / "doc.txt", "v1", self.now)
        self.sync()
        touch(self.a / "doc.txt", "v2-local", self.now + 50)
        rep = self.sync()
        self.assertEqual((rep.pushed_files, rep.safetynet_files), (1, 1))
        saved = list((self.b / ".docsync-safetynet").glob("*/doc.txt"))
        self.assertEqual(saved[0].read_text(), "v1")

    def test_safetynet_disabled(self):
        self.cfg.safetynet = False
        touch(self.a / "doc.txt", "a", self.now)
        self.sync()
        touch(self.b / "doc.txt", "b", self.now + 50)
        self.assertEqual(self.sync().safetynet_files, 0)
        self.assertFalse((self.a / ".docsync-safetynet").exists())

    def test_missing_root_fails_and_is_not_created(self):
        self.cfg.path = str(Path(self.tmp.name) / "nope")
        rep = self.sync()
        self.assertFalse(rep.ok)
        self.assertIn("root missing", rep.error)
        self.assertFalse((Path(self.tmp.name) / "nope").exists())

    def test_failing_backup_aborts_before_rsync(self):
        from docsync import transport
        touch(self.a / "doc.txt", "old", self.now)
        self.sync()
        touch(self.b / "doc.txt", "new", self.now + 50)
        sn = self.a / ".docsync-safetynet"
        sn.mkdir(exist_ok=True)
        os.chmod(sn, 0o500)
        called = {"n": 0}
        real = transport._run_rsync_once
        def spy(*a, **k):
            called["n"] += 1
            return real(*a, **k)
        transport._run_rsync_once = spy
        try:
            rep = self.sync()
        finally:
            transport._run_rsync_once = real
            os.chmod(sn, 0o700)
        self.assertFalse(rep.ok)
        self.assertEqual(called["n"], 0)
        self.assertEqual((self.a / "doc.txt").read_text(), "old")


class PlanOutputTests(unittest.TestCase):
    def test_dry_run_output_is_capped_unless_verbose(self):
        from docsync import engine
        from docsync.session import _print_plan
        plan = engine.Plan(pull=[f"f{i}" for i in range(50)], push=["a"])
        lines = []
        _print_plan(plan, lines.append, limit=20)
        self.assertEqual(sum(1 for ln in lines if ln.strip().startswith("pull ←") and "more" not in ln), 20)
        self.assertTrue(any("30 more" in ln for ln in lines))
        lines.clear()
        _print_plan(plan, lines.append, limit=None)
        self.assertEqual(len(lines), 51)


class DryRunBytesTests(unittest.TestCase):
    setUp, tearDown, sync = LocalSyncTests.setUp, LocalSyncTests.tearDown, LocalSyncTests.sync

    def test_dry_run_counts_bytes_of_mixed_case_names(self):
        touch(self.b / "Rechnung März.PDF", "x" * 3000, self.now)
        touch(self.a / "Upper/Case.TXT", "y" * 1000, self.now)
        rep = self.sync(dry=True)
        self.assertEqual((rep.pulled_bytes, rep.pushed_bytes), (3000, 1000))
