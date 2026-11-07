"""docsync.api: the engine entry point used by maccluster."""
import json
import os
import re
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from docsync import api, discover

ROOT = Path(__file__).resolve().parent.parent
HOLD = ("import sys, time; from pathlib import Path; from docsync import lock; "
        "fh = lock.acquire(Path(sys.argv[1])); print('held' if fh else 'busy', flush=True); time.sleep(60)")


def hold_lock(state: Path) -> subprocess.Popen:
    """A separate process that takes the run lock and keeps it; argv carries 'marker-arg'."""
    import sys
    env = dict(os.environ, PYTHONPATH=str(ROOT))
    proc = subprocess.Popen([sys.executable, "-c", HOLD, str(state), "marker-arg"], stdout=subprocess.PIPE,
                            text=True, env=env)
    assert proc.stdout.readline().strip() == "held"
    return proc


def touch(p: Path, text: str, mtime: int):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    os.utime(p, (mtime, mtime))


class ApiSyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.a, self.b, self.c = (self.base / n for n in ("A", "B", "C"))
        for d in (self.a, self.b, self.c):
            d.mkdir()
        self.now = int(time.time()) - 100

    def tearDown(self):
        self.tmp.cleanup()

    def req(self, peers, **kw):
        return api.SyncRequest(roots=[api.RootSpec("documents", str(self.a))], peers=peers,
                               state_dir=self.base / "state", trash=False, **kw)

    def test_two_local_peers_and_dict_shape(self):
        touch(self.a / "a.txt", "a", self.now)
        touch(self.b / "b.txt", "bb", self.now)
        events = []
        res = api.sync(self.req([api.PeerSpec("b", str(self.b), via="LAN"), api.PeerSpec("c", str(self.c))],
                                progress=lambda *e: events.append(e)))
        self.assertTrue(res.ok)
        self.assertEqual(res.exit_code, 0)
        d = res.to_dict()
        self.assertEqual(json.loads(json.dumps(d)), d)
        self.assertEqual(d["api_version"], api.API_VERSION)
        self.assertGreater(d["secs"], 0)  # the run's wall time, not the time since the report object
        self.assertEqual([p["peer"] for p in d["peers"]], ["b", "c"])
        pb = d["peers"][0]
        self.assertEqual((pb["root"], pb["via"], pb["pushed_files"], pb["pulled_files"]), ("documents", "LAN", 1, 1))
        self.assertIn({"name": "list", "secs": pb["phases"][1]["secs"]}, pb["phases"])
        self.assertTrue((self.c / "a.txt").exists() and (self.a / "b.txt").exists())
        self.assertIn(("b", "documents", "done", 3, 3), events)

    def test_unreachable_and_bad_host_do_not_raise(self):
        res = api.sync(self.req([api.PeerSpec("x", None, hosts_tried=["10.9.9.9"]),
                                 api.PeerSpec("y", str(self.base / "missing"))]))
        self.assertFalse(res.ok)
        self.assertEqual(res.exit_code, 2)
        errs = [p["error"] for p in res.to_dict()["peers"]]
        self.assertIn("10.9.9.9", errs[0])
        self.assertIn("not a directory", errs[1])

    def test_invalid_requests_raise_before_network(self):
        with self.assertRaisesRegex(ValueError, "no roots"):
            api.sync(api.SyncRequest(roots=[], peers=[api.PeerSpec("b", "h")]))
        with self.assertRaisesRegex(ValueError, "no peers"):
            api.sync(api.SyncRequest(roots=[api.RootSpec("d", str(self.a))], peers=[]))
        with self.assertRaises(ValueError):
            api.sync(self.req([api.PeerSpec("b", str(self.b))], conflict_policy="coinflip"))

    def test_lock_held_returns_exit_3(self):
        from docsync import lock
        fh = lock.acquire(self.base / "state")
        try:
            res = api.sync(self.req([api.PeerSpec("b", str(self.b))]))
        finally:
            fh.close()
        self.assertEqual((res.ok, res.exit_code), (False, 3))
        self.assertTrue(api.sync(self.req([api.PeerSpec("b", str(self.b))])).ok)

    def test_lock_file_names_holder(self):
        from docsync import lock
        state = self.base / "state"
        proc = hold_lock(state)
        try:
            before = (state / "run.lock").read_text()
            pid, started, cmd = before.splitlines()
            self.assertEqual(int(pid), proc.pid)
            self.assertLess(abs(float(started) - time.time()), 30)
            self.assertIn("marker-arg", cmd)
            self.assertEqual(lock.holder(state), {"pid": proc.pid, "started": float(started), "command": cmd})
            self.assertIsNone(lock.acquire(state))  # a failed acquire leaves the record intact
            self.assertEqual((state / "run.lock").read_text(), before)
        finally:
            proc.kill()
            proc.wait()
        (state / "run.lock").write_text("")
        self.assertIsNone(lock.holder(state))

    def test_dead_holder_is_replaced(self):
        from docsync import lock
        state = self.base / "state"
        state.mkdir()
        (state / "run.lock").write_text("999999\n1\nold command\n")
        fh = lock.acquire(state)
        try:
            self.assertIsNotNone(fh)
            h = lock.holder(state)
            self.assertEqual(h["pid"], os.getpid())
            self.assertNotEqual(h["command"], "old command")
        finally:
            fh.close()

    def test_lock_held_names_holder(self):
        proc = hold_lock(self.base / "state")
        try:
            res = api.sync(self.req([api.PeerSpec("b", str(self.b))]))
        finally:
            proc.kill()
            proc.wait()
        self.assertEqual(res.exit_code, 3)
        self.assertIn(f"another run is in progress (pid {proc.pid}, running 0 min, ", res.error)
        self.assertIn("marker-arg", res.error)

    def test_per_root_peer_path(self):
        d2 = self.base / "B2"
        d2.mkdir()
        touch(self.a / "x.txt", "x", self.now)
        res = api.sync(self.req([api.PeerSpec("b", "/", paths={"documents": str(d2)})]))
        # host "/" means "this machine"; the per-root path decides where it lands
        self.assertTrue(res.ok, res.to_dict()["peers"][0]["error"])
        self.assertTrue((d2 / "x.txt").exists())

    def test_root_slash_is_refused(self):
        from docsync.transport import LocalTransport, TransportError
        with self.assertRaisesRegex(TransportError, "refusing"):
            LocalTransport(Path("/")).check()


class ApiResolveTests(unittest.TestCase):
    def test_resolve_peer_thunderbolt_first(self):
        orig = (discover.order_hosts, discover.ping)
        discover.order_hosts = lambda hosts: [("10.42.0.2", "Thunderbolt"), ("mini.local", "LAN")]
        discover.ping = lambda h, *a, **k: 0.5
        try:
            self.assertEqual(api.resolve_peer(["mini.local", "10.42.0.2"]), ("10.42.0.2", "Thunderbolt"))
        finally:
            discover.order_hosts, discover.ping = orig


class EngineHygieneTests(unittest.TestCase):
    def test_engine_never_exits_or_prints(self):
        root = Path(__file__).resolve().parent.parent / "docsync"
        for name in ("session.py", "api.py", "transport.py", "safetynet.py", "fidelity.py", "lock.py"):
            for i, line in enumerate((root / name).read_text().splitlines(), 1):
                code = line.split("#", 1)[0]
                self.assertNotIn("sys.exit", code, f"{name}:{i}")
                if re.search(r"\bprint\(", code):
                    self.assertIn("verbose", "\n".join((root / name).read_text().splitlines()[max(0, i - 4):i]), f"{name}:{i}")


class PackagingTests(unittest.TestCase):
    def test_pyproject_metadata(self):
        import tomllib
        root = Path(__file__).resolve().parent.parent
        meta = tomllib.loads((root / "pyproject.toml").read_text())
        self.assertEqual(meta["project"]["name"], "docsync-lan")  # "docsync" on PyPI is someone else's
        self.assertEqual(meta["project"]["scripts"]["docsync"], "docsync.cli:main")
        self.assertTrue((root / "docsync" / "resources" / "docsync-advisor.swift").exists())
