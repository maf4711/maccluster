"""Symlinks are never written through, replaced or deleted (collected via test_sync_local).

Incident 2026-11-08: ~/.claude/projects/<p>/memory was a symlink into a git-versioned dotfiles dir;
the first llm-claude sync pulled the peer's files and rsync replaced the symlink with a real dir.
Remote peers go through tests/fake_ssh.py, so RemoteTransport runs LISTER_PY under /usr/bin/python3."""
import ast
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from docsync import engine, manifest
from docsync.config import Config, Root
from docsync.session import base_file, run_peer
from docsync.transport import LocalTransport, RemoteTransport

FAKE_SSH = str(Path(__file__).resolve().parent / "fake_ssh.py")
PY39 = "/usr/bin/python3"
INCLUDES = ["CLAUDE.md", "projects/*/memory/**"]
LOCAL = socket.gethostname().split(".")[0]


def touch(p: Path, text: str = "x", mtime: int | None = None):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    if mtime is not None:
        os.utime(p, (mtime, mtime))


def tree(d: Path) -> dict[str, str]:
    """relpath -> content of every regular file below d (symlinks not followed)."""
    out = {}
    for dirpath, _, files in os.walk(d):
        for f in files:
            full = Path(dirpath) / f
            if not full.is_symlink():
                out[str(full.relative_to(d))] = full.read_text()
    return out


class SymlinkTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.a, self.b = self.base / "A" / ".claude", self.base / "B" / ".claude"
        self.state, self.pstate = self.base / "state", self.base / "pstate"
        self.dot = self.base / "dotfiles" / "memory"  # the symlink target ("elsewhere")
        for d in (self.a, self.b, self.dot):
            d.mkdir(parents=True)
        touch(self.dot / "keep.md", "versioned")
        self.now = int(time.time()) - 100

    def tearDown(self):
        self.tmp.cleanup()

    def remote(self, root: Path) -> RemoteTransport:
        t = RemoteTransport("fakepeer", str(root))
        t.ssh_cmd = t.bulk_ssh_cmd = [sys.executable, FAKE_SSH, "--state", str(self.pstate)]
        return t

    def sync(self, peer="b", includes=None, remote=False, dry=False):
        cfg = Config(path=str(self.a), trash=False).for_root(Root("llm", str(self.a), includes=includes))
        t = self.remote(self.b) if remote else LocalTransport(self.b)
        rep = run_peer(cfg, peer, t, dry=dry, state_dir=self.state, log=lambda *a: None, root_name="llm")
        self.assertTrue(rep.ok, rep.error)
        return rep

    def link(self, at: Path, target: Path):
        at.parent.mkdir(parents=True, exist_ok=True)
        os.symlink(target, at)

    def assert_link(self, at: Path, target: Path):
        self.assertTrue(at.is_symlink(), f"{at} is no longer a symlink")
        self.assertEqual(os.readlink(at), str(target))

    def base_keys(self, peer="b"):
        return set(manifest.load(base_file(peer, self.state, "llm")) or {})

    def test_pull_never_replaces_local_dir_symlink(self):
        self.link(self.a / "projects/x/memory", self.dot)
        touch(self.b / "projects/x/memory/m1.md", "peer 1")
        touch(self.b / "projects/x/memory/m2.md", "peer 2")
        touch(self.b / "CLAUDE.md", "claude")
        for i, includes in enumerate((None, INCLUDES)):
            with self.subTest(includes=includes):
                peer = f"b{i}"
                for _ in range(2):  # the second run must not turn the skip into a deletion
                    rep = self.sync(peer, includes)
                    self.assert_link(self.a / "projects/x/memory", self.dot)
                    self.assertEqual(tree(self.dot), {"keep.md": "versioned"})
                    self.assertIn(f"skipped (symlink on {LOCAL}): 2 path(s)", rep.warnings)
                    self.assertEqual(rep.deleted_remote, 0)
                self.assertEqual((self.a / "CLAUDE.md").read_text(), "claude")
                self.assertEqual(sorted(tree(self.b / "projects")), ["x/memory/m1.md", "x/memory/m2.md"])
                self.assertFalse({k for k in self.base_keys(peer) if k.startswith("projects/x/")})
                self.assertIn(f"skipped (symlink on {LOCAL})", rep.render())

    def test_push_never_replaces_peer_dir_symlink(self):
        self.link(self.b / "projects/x/memory", self.dot)
        touch(self.a / "projects/x/memory/m1.md", "local 1")
        touch(self.a / "projects/x/memory/m2.md", "local 2")
        touch(self.a / "CLAUDE.md", "claude")
        for i, includes in enumerate((None, INCLUDES)):
            with self.subTest(includes=includes):
                peer = f"b{i}"
                for _ in range(2):
                    rep = self.sync(peer, includes, remote=True)
                    self.assert_link(self.b / "projects/x/memory", self.dot)
                    self.assertEqual(tree(self.dot), {"keep.md": "versioned"})
                    self.assertIn(f"skipped (symlink on {peer}): 2 path(s)", rep.warnings)
                    self.assertEqual(rep.deleted_local, 0)
                self.assertEqual((self.b / "CLAUDE.md").read_text(), "claude")
                self.assertEqual(sorted(tree(self.a / "projects")), ["x/memory/m1.md", "x/memory/m2.md"])
                self.assertFalse({k for k in self.base_keys(peer) if k.startswith("projects/x/")})

    def test_listers_report_symlinks(self):
        self.link(self.b / "projects/x/memory", self.dot)
        self.link(self.b / "CLAUDE.md", self.dot / "keep.md")
        self.link(self.b / "projects/y/memory/dangling.md", self.base / "nowhere")
        touch(self.b / "projects/y/memory/ok.md")
        want = {"projects/x/memory", "CLAUDE.md", "projects/y/memory/dangling.md"}
        t = self.remote(self.b)
        for kw in ({}, {"includes": INCLUDES}):
            with self.subTest(**kw):
                links: set[str] = set()
                m = t.manifest([".docsync*"], set(), links=links, **kw)
                self.assertEqual((sorted(m), links), (["projects/y/memory/ok.md"], want))
        links = set()
        with mock.patch.dict(os.environ, {"PATH": "/bin:/usr/bin:/usr/sbin:/sbin"}), \
                mock.patch.object(t, "ssh", side_effect=self._no_python(t.ssh)):  # LISTER_SH fallback
            self.assertEqual(sorted(t.manifest([".docsync*"], set(), links=links)), ["projects/y/memory/ok.md"])
        self.assertEqual(links, want)
        local: set[str] = set()
        manifest.scan(self.b, [".docsync*"], set(), links=local)
        self.assertEqual(local, want)
        ast.parse(manifest.LISTER_PY, feature_version=(3, 9))

    @staticmethod
    def _no_python(real):
        def ssh(cmd, **kw):  # the python3 lister "fails" like on a peer without python3
            return real("exit 1" if "python3 -" in cmd else cmd, **kw)
        return ssh

    def test_file_symlink_not_replaced(self):
        for side in ("local", "remote"):
            with self.subTest(side=side):
                shutil.rmtree(self.a)
                shutil.rmtree(self.b)
                holder, other = (self.a, self.b) if side == "local" else (self.b, self.a)
                touch(self.dot / "claude.md", "versioned claude", self.now - 50)
                self.link(holder / "CLAUDE.md", self.dot / "claude.md")
                self.link(holder / "notes.md", self.base / "nowhere")  # dangling
                touch(other / "CLAUDE.md", "newer regular file", self.now)
                touch(other / "notes.md", "notes", self.now)
                touch(other / "settings.json", "{}", self.now)
                peer = f"f-{side}"
                for _ in range(2):
                    rep = self.sync(peer, remote=True)
                    self.assert_link(holder / "CLAUDE.md", self.dot / "claude.md")
                    self.assert_link(holder / "notes.md", self.base / "nowhere")
                    self.assertEqual((self.dot / "claude.md").read_text(), "versioned claude")
                    self.assertEqual((other / "CLAUDE.md").read_text(), "newer regular file")
                    self.assertIn(f"skipped (symlink on {LOCAL if side == 'local' else peer}): 2 path(s)", rep.warnings)
                    self.assertEqual((rep.conflicts, rep.deleted_local, rep.deleted_remote), ([], 0, 0))
                self.assertTrue((holder / "settings.json").is_file())
                self.assertEqual(self.base_keys(peer), {"settings.json"})

    def test_no_delete_under_symlink(self):
        for side in ("local", "remote"):
            with self.subTest(side=side):
                shutil.rmtree(self.a)
                shutil.rmtree(self.b)
                shutil.rmtree(self.dot)
                touch(self.a / "projects/x/memory/m.md", "memory", self.now)
                touch(self.a / "CLAUDE.md", "c", self.now)
                peer = f"d-{side}"
                self.sync(peer, INCLUDES, remote=True)
                self.assertIn("projects/x/memory/m.md", self.base_keys(peer))
                holder, other = (self.a, self.b) if side == "local" else (self.b, self.a)
                # the holder moves its dir into a dotfiles repo and leaves a symlink behind
                self.dot.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(holder / "projects/x/memory"), str(self.dot))
                self.link(holder / "projects/x/memory", self.dot)
                for _ in range(2):
                    rep = self.sync(peer, INCLUDES, remote=True)
                    self.assertEqual((rep.deleted_local, rep.deleted_remote, rep.deletes_skipped), (0, 0, 0))
                    self.assert_link(holder / "projects/x/memory", self.dot)
                    self.assertEqual((self.dot / "m.md").read_text(), "memory")
                    self.assertEqual((other / "projects/x/memory/m.md").read_text(), "memory")
                    self.assertNotIn("projects/x/memory/m.md", self.base_keys(peer))  # dropped: no later delete
                    self.assertIn(f"skipped (symlink on {LOCAL if side == 'local' else peer}): 1 path(s)", rep.warnings)

    def test_roots_without_symlinks_unchanged(self):
        for rel, side, t in (("a.txt", self.a, 0), ("sub/b.txt", self.b, 0), ("both.txt", self.a, 0),
                             ("both.txt", self.b, 10), ("deep/x/y.md", self.a, 5)):
            touch(side / rel, f"{side.parent.name}:{rel}", self.now + t)
        expect = engine.plan(manifest.scan(self.a, Config().excludes), manifest.scan(self.b, Config().excludes), None)
        rep = self.sync(remote=True)
        n = len(expect.conflicts)  # each conflict moves one file and its conflict copy
        self.assertEqual((rep.pushed_files, rep.pulled_files), (len(expect.push) + n, len(expect.pull) + n))
        self.assertEqual([(p, w) for p, w, _ in rep.conflicts], expect.conflicts)
        self.assertFalse([w for w in rep.warnings if "symlink" in w], rep.warnings)
        self.assertNotIn("symlink", rep.render())
        self.assertEqual(sorted(tree(self.a)), sorted(tree(self.b)))
        for args in ([], ["case", *INCLUDES]):  # no symlinks: no marker records at all
            res = subprocess.run([PY39, "-", str(self.a), *args], input=manifest.LISTER_PY,
                                 text=True, capture_output=True, timeout=60)
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertNotIn("\t-3\t", res.stdout)

    def test_link_at_include_dir_name(self):
        # a dangling link or a file link where an include expects a dir (projects/p/memory)
        for side in ("local", "remote"):
            for kind in ("dangling", "file"):
                with self.subTest(side=side, kind=kind):
                    shutil.rmtree(self.a)
                    shutil.rmtree(self.b)
                    holder, other = (self.a, self.b) if side == "local" else (self.b, self.a)
                    target = self.base / "nowhere" if kind == "dangling" else self.dot / "keep.md"
                    self.link(holder / "projects/p/memory", target)
                    touch(other / "projects/p/memory/x.md", "x")
                    peer = f"i-{side}-{kind}"
                    for _ in range(2):
                        rep = self.sync(peer, INCLUDES, remote=True)
                        self.assert_link(holder / "projects/p/memory", target)
                        self.assertIn(f"skipped (symlink on {LOCAL if side == 'local' else peer}): 1 path(s)",
                                      rep.warnings)
                        self.assertEqual((rep.deleted_local, rep.deleted_remote), (0, 0))
                    self.assertEqual((other / "projects/p/memory/x.md").read_text(), "x")
                    self.assertEqual(tree(self.dot), {"keep.md": "versioned"})

    def test_unlink_after_skip_pulls_back(self):
        # synced → replaced by a symlink (skipped) → symlink removed → the files come back, no delete
        for side in ("local", "remote"):
            with self.subTest(side=side):
                shutil.rmtree(self.a)
                shutil.rmtree(self.b)
                shutil.rmtree(self.dot)
                touch(self.a / "projects/x/memory/x.md", "memory", self.now)
                peer = f"u-{side}"
                self.sync(peer, INCLUDES, remote=True)
                holder, other = (self.a, self.b) if side == "local" else (self.b, self.a)
                self.dot.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(holder / "projects/x/memory"), str(self.dot))
                self.link(holder / "projects/x/memory", self.dot)
                self.sync(peer, INCLUDES, remote=True)
                (holder / "projects/x/memory").unlink()
                rep = self.sync(peer, INCLUDES, remote=True)
                self.assertEqual((rep.deleted_local, rep.deleted_remote, rep.deletes_skipped), (0, 0, 0))
                for d in (holder, other):
                    self.assertEqual((d / "projects/x/memory/x.md").read_text(), "memory")
                shutil.rmtree(holder / "projects/x/memory")  # link it again: skipped, never a delete
                self.link(holder / "projects/x/memory", self.dot)
                rep = self.sync(peer, INCLUDES, remote=True)
                self.assertEqual((rep.deleted_local, rep.deleted_remote), (0, 0))
                self.assertEqual((other / "projects/x/memory/x.md").read_text(), "memory")
                self.assertEqual(tree(self.dot), {"x.md": "memory"})

    def test_transport_links_kwarg(self):
        # links goes to a transport taking **kw; one without either gets a warning, not silence
        self.link(self.b / "projects/x/memory", self.dot)
        touch(self.a / "projects/x/memory/m.md")

        class KwSpy(LocalTransport):
            def manifest(self, excludes, dataless=None, **kw):
                return super().manifest(excludes, dataless, **kw)

        class OldSpy(LocalTransport):
            def manifest(self, excludes, dataless=None):  # the 1.2.0 transport interface
                return super().manifest(excludes, dataless)
        cfg = Config(path=str(self.a), trash=False).for_root(Root("llm", str(self.a)))
        logs: list[str] = []
        rep = run_peer(cfg, "kw", KwSpy(self.b), dry=True, state_dir=self.state, log=logs.append, root_name="llm")
        self.assertIn("skipped (symlink on kw): 1 path(s)", rep.warnings)
        self.assertFalse([w for w in rep.warnings if "unavailable" in w], rep.warnings)
        rep = run_peer(cfg, "old", OldSpy(self.b), dry=True, state_dir=self.state, log=logs.append, root_name="llm")
        self.assertIn("peer symlink protection unavailable for OldSpy", rep.warnings)
        self.assertTrue(any("peer symlink protection unavailable for OldSpy" in m for m in logs), logs)
