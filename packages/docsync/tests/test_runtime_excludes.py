"""Per-Mac runtime state of agent tools (.claude-flow, .swarm, .hive-mind) is never synced: each Mac
rewrites it all the time, so syncing it only produced conflict copies (node-b, 2026-11-09)."""
import os
import tempfile
import time
import unittest
from pathlib import Path

from docsync.config import RUNTIME_STATE_EXCLUDES, Config, Peer, parse_config
from docsync.session import run_peer
from docsync.transport import LocalTransport


def touch(p: Path, text: str, mtime: int):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    os.utime(p, (mtime, mtime))


class RuntimeStateExcludeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.a, self.b, self.state = base / "A" / "Documents", base / "B" / "Documents", base / "state"
        self.a.mkdir(parents=True)
        self.b.mkdir(parents=True)
        self.now = int(time.time()) - 1000

    def tearDown(self):
        self.tmp.cleanup()

    def sync(self, cfg):
        return run_peer(cfg, "b", LocalTransport(self.b), dry=False, state_dir=self.state, log=lambda *a: None)

    def test_names(self):
        self.assertEqual(RUNTIME_STATE_EXCLUDES, [".claude-flow", ".swarm", ".hive-mind"])

    def test_every_root_excludes_runtime_state_even_with_an_explicit_list(self):
        cfg = parse_config('path = "~/Documents"\nexcludes = [".DS_Store"]\n'
                           '[[roots]]\nname = "desktop"\npath = "~/Desktop"\n')
        for root in cfg.effective_roots():
            ex = cfg.for_root(root).excludes
            for name in RUNTIME_STATE_EXCLUDES:
                self.assertIn(name, ex, root.name)
            self.assertIn(".DS_Store", ex)

    def test_switch_keeps_both_sides_and_makes_no_conflicts(self):
        touch(self.a / ".claude-flow/daemon-state.json", "a0", self.now)
        touch(self.a / "proj/.swarm/memory.db", "m0", self.now)
        touch(self.a / "doc.txt", "d", self.now)
        old = Config(path=str(self.a), trash=False, excludes=[".DS_Store"], peers=[Peer("b", [str(self.b)])])
        self.assertTrue(self.sync(old).ok)                      # before: state was synced
        self.assertTrue((self.b / ".claude-flow/daemon-state.json").exists())
        # each Mac rewrites its own state
        touch(self.a / ".claude-flow/daemon-state.json", "a1", self.now + 10)
        touch(self.b / ".claude-flow/daemon-state.json", "b1", self.now + 20)
        touch(self.b / "proj/.swarm/memory.db", "m1", self.now + 20)
        new = old.for_root(old.effective_roots()[0])
        rep = self.sync(new)
        self.assertTrue(rep.ok, rep.error)
        self.assertEqual((self.a / ".claude-flow/daemon-state.json").read_text(), "a1")
        self.assertEqual((self.b / ".claude-flow/daemon-state.json").read_text(), "b1")
        self.assertEqual((self.a / "proj/.swarm/memory.db").read_text(), "m0")
        self.assertEqual((self.b / "proj/.swarm/memory.db").read_text(), "m1")
        everything = [p for side in (self.a, self.b) for p in side.rglob("*")]
        self.assertFalse([p for p in everything if ".conflict-" in p.name])
        self.assertTrue((self.b / "doc.txt").exists())          # real documents still sync


if __name__ == "__main__":
    unittest.main()
