"""rsync partial files never land inside a synced root (collected via test_sync_local).

Incident 2026-11-25: `--partial-dir=.docsync-partial` (relative) made rsync keep failed transfers of
unreadable iCloud placeholders inside every destination directory of ~/Documents: 23,275 zero-filled
files that iCloud Drive then uploaded. Remote peers go through tests/fake_ssh.py."""
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from docsync.config import Config
from docsync.manifest import SF_DATALESS
from docsync.safetynet import SN_DIR
from docsync.session import run_peer
from docsync.transport import LocalTransport, RemoteTransport

FAKE_SSH = str(Path(__file__).resolve().parent / "fake_ssh.py")
PARTIAL = ".docsync-partial"


def touch(p: Path, data: bytes = b"x", mtime: int | None = None):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    if mtime is not None:
        os.utime(p, (mtime, mtime))


def partial_dirs(*roots: Path) -> list[str]:
    return [os.path.join(d, n) for r in roots for d, dirs, _ in os.walk(r) for n in dirs if n == PARTIAL]


class PartialDirTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name).resolve()
        self.a, self.b, self.c = (self.base / n / "Documents" for n in "ABC")
        self.state, self.pstate = self.base / "state", self.base / "pstate"
        for d in (self.a, self.b, self.c):
            d.mkdir(parents=True)
        self.cfg = Config(path=str(self.a), trash=False)
        self.now = int(time.time()) - 100

    def tearDown(self):
        for d, _dirs, files in os.walk(self.base):
            for f in files:
                os.chmod(os.path.join(d, f), 0o644)
        self.tmp.cleanup()

    def sync(self, peer: str, t):
        rep = run_peer(self.cfg, peer, t, state_dir=self.state, log=lambda *a: None)
        self.assertTrue(rep.ok, rep.error)
        return rep

    def remote(self, root: Path) -> RemoteTransport:
        t = RemoteTransport("fakepeer", str(root))
        t.ssh_cmd = t.bulk_ssh_cmd = [sys.executable, FAKE_SSH, "--state", str(self.pstate)]
        return t

    def rsync_argvs(self, fn) -> list[list[str]]:
        """Run fn() and return the argv of every rsync it started (real rsync still runs)."""
        seen, real = [], subprocess.Popen

        def spy(cmd, *a, **kw):
            if isinstance(cmd, list) and any("--files-from=" in str(x) for x in cmd):
                seen.append(list(cmd))
            return real(cmd, *a, **kw)
        with mock.patch("docsync.transport.subprocess.Popen", side_effect=spy):
            fn()
        return seen

    def assert_outside(self, argvs: list[list[str]], roots) -> set[str]:
        dirs = set()
        for cmd in argvs:
            vals = [x.split("=", 1)[1] for x in cmd if x.startswith("--partial-dir=")]
            self.assertEqual(len(vals), 1, cmd)
            d = vals[0]
            # rsync's .<name>.XXXXXX temp files live below the partial dir too, never in a root
            self.assertEqual([x for x in cmd if x.startswith("--temp-dir=")], [f"--temp-dir={d}/tmp"], cmd)
            self.assertTrue(os.path.isdir(f"{d}/tmp"), d)
            self.assertTrue(os.path.isabs(d), d)
            for r in roots:
                self.assertFalse(Path(d).resolve().is_relative_to(r.resolve()), f"{d} under {r}")
            dirs.add(d)
        return dirs

    def test_partial_dir_is_outside_synced_root(self):
        touch(self.a / "push.txt", b"p", self.now)
        touch(self.b / "pull.txt", b"q", self.now)
        touch(self.c / "pull-c.txt", b"r", self.now)
        roots = (self.a, self.b, self.c)
        # local peers b and c: push and pull, one partial dir per peer
        argv_b = self.rsync_argvs(lambda: self.sync("b", LocalTransport(self.b)))
        argv_c = self.rsync_argvs(lambda: self.sync("c", LocalTransport(self.c)))
        self.assertEqual(len(argv_b), 2, argv_b)  # push + pull
        db, dc = self.assert_outside(argv_b, roots), self.assert_outside(argv_c, roots)
        self.assertEqual(len(db), 1)
        self.assertNotEqual(db, dc)
        self.assertTrue(all(Path(d).resolve().is_relative_to(self.state) for d in db | dc), db | dc)
        # ssh peer: push uses an absolute dir in the peer's state dir, pull one in ours
        touch(self.a / "push2.txt", b"p2", self.now)
        r = self.base / "R" / "Documents"
        touch(r / "pull-r.txt", b"s", self.now)
        argv_r = self.rsync_argvs(lambda: self.sync("r", self.remote(r)))
        self.assertEqual(len(argv_r), 2, argv_r)
        dr = self.assert_outside(argv_r, (*roots, r))
        self.assertEqual(len(dr), 2)
        self.assertEqual(sorted(Path(d).resolve().is_relative_to(self.pstate) for d in dr), [False, True])
        self.assertEqual(partial_dirs(*roots, r), [])
        self.assertTrue((r / "push2.txt").exists() and (self.a / "pull-r.txt").exists())

    def test_unreadable_source_leaves_nothing_in_root(self):
        bad = self.b / "Fotos" / "a.jpg"  # like an iCloud placeholder that cannot be read on the sender
        touch(bad, b"\xff" * 300_000, self.now)
        touch(self.b / "Fotos" / "ok.jpg", b"ok", self.now)
        os.chmod(bad, 0)
        rep = self.sync("b", LocalTransport(self.b))
        self.assertFalse((self.a / "Fotos" / "a.jpg").exists())
        self.assertTrue((self.a / "Fotos" / "ok.jpg").exists())
        self.assertEqual(partial_dirs(self.a, self.b), [])
        self.assertEqual(rep.skipped, 1)
        self.assertTrue(any("a.jpg" in w for w in rep.warnings), rep.warnings)

    def test_stale_zero_partials_go_to_safetynet(self):
        touch(self.a / "x" / PARTIAL / "a.jpg", b"\0" * 70_000)
        touch(self.a / "x" / PARTIAL / "empty.pdf", b"")
        real = b"%PDF-1.7 real bytes" * 100
        touch(self.a / "y" / PARTIAL / "b.pdf", b"\0" * 10 + real)
        touch(self.a / "x" / "keep.txt", b"k", self.now)
        rep = self.sync("b", LocalTransport(self.b))
        self.assertFalse(os.path.exists(self.a / "x" / PARTIAL))
        moved = [os.path.join(d, f) for d, _, fs in os.walk(self.a / SN_DIR) for f in fs if f == "a.jpg"]
        self.assertEqual(len(moved), 1, moved)
        self.assertEqual(Path(moved[0]).read_bytes(), b"\0" * 70_000)
        self.assertEqual(hashlib.sha256((self.a / "y" / PARTIAL / "b.pdf").read_bytes()).hexdigest(),
                         hashlib.sha256(b"\0" * 10 + real).hexdigest())
        kept = [w for w in rep.warnings if PARTIAL in w and "kept" in w]
        self.assertEqual(len(kept), 1, rep.warnings)
        self.assertIn("y/" + PARTIAL, kept[0])
        self.assertTrue((self.b / "x" / "keep.txt").exists())
        self.assertEqual(partial_dirs(self.b), [])

    def push_argv_after_failed_setup(self, ssh_fake) -> tuple[list[str], list[list[str]]]:
        r = self.base / "R" / "Documents"
        r.mkdir(parents=True)
        touch(self.a / "f.txt", b"f", self.now)
        t = self.remote(r)
        t.ssh = ssh_fake
        warns = t.use_partial_dir(self.state / "partial" / "x", "documents-here")
        argvs = self.rsync_argvs(lambda: t.push(self.a, ["f.txt"], False, False))
        self.assertTrue((r / "f.txt").exists())
        return warns, argvs

    def test_peer_partial_dir_setup_failure_pushes_without_it(self):
        def rc1(cmd, **kw):
            return subprocess.CompletedProcess(cmd, 1, "", "mkdir: permission denied")

        def hang(cmd, **kw):
            raise subprocess.TimeoutExpired(cmd, kw.get("timeout", 20))

        def gone(cmd, **kw):
            raise OSError("ssh: no such file")
        for fake in (rc1, hang, gone):
            with self.subTest(fake.__name__):
                warns, argvs = self.push_argv_after_failed_setup(fake)
                self.assertEqual(len(warns), 1, warns)
                self.assertIn("no partial dir on peer", warns[0])
                self.assertEqual(len(argvs), 1)
                self.assertFalse(any(x.startswith(("--partial-dir", "--temp-dir")) for x in argvs[0]), argvs[0])
                shutil.rmtree(self.base / "R")

    def test_partial_dir_inside_a_root_is_dropped(self):
        touch(self.a / "f.txt", b"f", self.now)
        # local: the state dir lies inside the local root (root = ~ style) → no partial args at all
        lt = LocalTransport(self.b)
        warns = lt.use_partial_dir(self.a / ".local/state/docsync/partial/x", "k", self.a)
        self.assertEqual(len(warns), 1, warns)
        self.assertIn("inside", warns[0])
        argvs = self.rsync_argvs(lambda: lt.push(self.a, ["f.txt"], False, False))
        self.assertFalse(any(x.startswith(("--partial-dir", "--temp-dir")) for x in argvs[0]), argvs[0])
        self.assertFalse((self.a / ".local").exists())
        # ssh peer whose root contains its state dir (root = ~): push without, pull with partial dir
        home = self.base / "peerhome"
        home.mkdir()
        t = self.remote(home)
        t.ssh_cmd = t.bulk_ssh_cmd = [sys.executable, FAKE_SSH, "--state", str(home / ".local/state/docsync")]
        warns = t.use_partial_dir(self.state / "partial" / "r", "documents-here", self.a)
        self.assertEqual(len(warns), 1, warns)
        self.assertIn("inside the peer's synced root", warns[0])
        argvs = self.rsync_argvs(lambda: t.push(self.a, ["f.txt"], False, False))
        self.assertFalse(any(x.startswith(("--partial-dir", "--temp-dir")) for x in argvs[0]), argvs[0])
        self.assertTrue((home / "f.txt").exists())
        self.assertTrue(any(x.startswith("--partial-dir=") for x in t.partial_args["pull"]))

    def test_old_files_in_state_partial_dir_are_pruned(self):
        from docsync import partials
        old = self.state / "partial" / "documents-zz" / "old.jpg"
        fresh = self.state / "partial" / "documents-zz" / "fresh.jpg"
        touch(old, b"o")
        touch(fresh, b"f")
        week = time.time() - 8 * 86400
        os.utime(old, (week, week))
        self.assertEqual(partials.prune(self.state / "partial", now=time.time() + 8 * 86400), 2)  # ctime counts
        self.assertTrue(old.parent.is_dir())  # directories stay
        touch(old, b"o")
        touch(fresh, b"f")
        os.utime(old, (week, week))
        touch(self.a / "x.txt", b"x", self.now)
        real_lstat = os.lstat

        def aged(p, *a, **kw):  # the run sees old.jpg with an 8-day-old ctime too
            st = real_lstat(p, *a, **kw)
            if str(p).endswith("old.jpg"):
                return os.stat_result((*st[:7], week, week, week))
            return st
        with mock.patch("docsync.partials.os.lstat", side_effect=aged):
            self.sync("b", LocalTransport(self.b))
        self.assertFalse(old.exists())
        self.assertTrue(fresh.exists())

    def test_sweep_keeps_dataless_and_symlinked_partials_unread(self):
        from types import SimpleNamespace
        evicted = self.a / "e" / PARTIAL / "cloud.jpg"
        touch(evicted, b"\0" * 4096)  # zeros on disk, but flagged as evicted to iCloud
        outside = self.base / "elsewhere"
        touch(outside / "z.bin", b"\0" * 4096)
        (self.a / "s").mkdir()
        os.symlink(outside, self.a / "s" / PARTIAL)
        real_lstat, real_open, opened = os.lstat, open, []

        def lstat(p, *a, **kw):
            st = real_lstat(p, *a, **kw)
            if str(p).endswith("cloud.jpg"):
                return SimpleNamespace(st_mode=st.st_mode, st_size=st.st_size, st_flags=SF_DATALESS)
            return st

        def spy_open(p, *a, **kw):
            opened.append(str(p))
            return real_open(p, *a, **kw)
        with mock.patch("docsync.partials.os.lstat", side_effect=lstat), \
                mock.patch("docsync.partials.open", side_effect=spy_open, create=True):
            rep = self.sync("b", LocalTransport(self.b))
        self.assertEqual(opened, [])  # neither file was read (reading an evicted file downloads it)
        self.assertTrue(evicted.exists())
        self.assertTrue((self.a / "s" / PARTIAL).is_symlink())
        self.assertTrue((outside / "z.bin").exists())
        kept = sorted(w.rsplit(": ", 1)[1] for w in rep.warnings if PARTIAL in w and "kept" in w)
        self.assertEqual(kept, ["e/" + PARTIAL, "s/" + PARTIAL])
        self.assertFalse((self.a / SN_DIR).exists() and any((self.a / SN_DIR).rglob("*.jpg")))
