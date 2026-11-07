"""DeadlineTests (--max-runtime, process-wide watchdog). Collected through test_sync_local, which
re-exports the class; the module name has no test_ prefix so discovery does not run it twice."""
import os
import tempfile
import time
import unittest
from pathlib import Path

from docsync.config import Config


# Runs the real CLI in a subprocess; peers are local trees, so resolve() is stubbed and watch sleeps short.
WRAP = ("import sys, time, types; from docsync import cli; cli.resolve = lambda p, log: (p.hosts[0], 'LAN'); "
        "t = types.ModuleType('t'); t.__dict__.update(vars(time)); t.sleep = lambda s: time.sleep(0.2); cli.time = t; "
        "raise SystemExit(cli.main(sys.argv[1:]))")


class DeadlineTests(unittest.TestCase):
    def setUp(self):
        from docsync.config import Peer, save_config
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.a, self.b, self.state = base / "A" / "Documents", base / "B" / "Documents", base / "state"
        self.a.mkdir(parents=True)
        self.b.mkdir(parents=True)
        (self.a / "x.txt").write_text("x")
        self.cfgdir = self.state.parent / "cfg"
        save_config(Config(path=str(self.a), trash=False, peers=[Peer("b", [str(self.b)])]), self.cfgdir / "config.toml")

    def tearDown(self):
        self.tmp.cleanup()

    def spawn(self, args, hang=""):
        import subprocess, sys
        env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parent.parent), DOCSYNC_TEST_HANG=hang,
                   DOCSYNC_CONFIG_DIR=str(self.cfgdir), DOCSYNC_STATE_DIR=str(self.state))
        return subprocess.Popen([sys.executable, "-c", WRAP, *args], env=env, text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def history(self):
        f = self.state / "history.log"
        return f.read_text() if f.exists() else ""

    def test_hung_peer_ends_process(self):
        import subprocess
        from docsync import lock
        t0, kids = time.monotonic(), set()
        p = self.spawn(["sync", "--all", "--quiet", "--no-notify", "--max-runtime", "2"], hang="1")
        while p.poll() is None and time.monotonic() - t0 < 20:
            out = subprocess.run(["pgrep", "-P", str(p.pid)], capture_output=True, text=True).stdout
            kids |= {int(x) for x in out.split()}
            time.sleep(0.1)
        out, err = p.communicate(timeout=5)
        self.assertEqual(p.returncode, 4, err)
        self.assertLess(time.monotonic() - t0, 12)
        self.assertIn("deadline of 2 s exceeded", err)
        self.assertIn("deadline of 2 s exceeded", self.history())
        self.assertTrue(kids, "the hung listing should have had a child process")
        time.sleep(0.5)
        for k in kids:
            with self.assertRaises(ProcessLookupError):
                os.kill(k, 0)
        self.assertEqual(list(self.state.glob("base-*")), [])
        fh = lock.acquire(self.state)
        self.assertIsNotNone(fh)
        fh.close()

    def test_max_runtime_parsing(self):
        from docsync import cli, watchdog
        p = self.spawn(["sync", "--max-runtime", "-1"])
        _, err = p.communicate(timeout=20)
        self.assertEqual(p.returncode, 2)
        self.assertIn("--max-runtime must be >= 0", err)
        self.assertEqual(cli.build_parser().parse_args(["sync"]).max_runtime, 1800)
        seen, orig = [], cli.cmd_sync
        cli.cmd_sync = lambda a, dry: seen.append(watchdog.active()) or 0
        try:
            self.assertEqual(cli.main(["sync", "--max-runtime", "0"]), 0)
        finally:
            cli.cmd_sync = orig
        self.assertEqual(seen, [False])
        p = self.spawn(["sync", "--all", "--quiet", "--no-notify", "--max-runtime", "0"])
        _, err = p.communicate(timeout=30)
        self.assertEqual(p.returncode, 0, err)

    def test_cli_passes_max_runtime(self):
        from docsync import cli, watchdog
        seen, orig = [], (cli.cmd_sync, watchdog.arm)
        cli.cmd_sync, watchdog.arm = (lambda a, dry: 0), (lambda s, cb: seen.append(s))
        try:
            cli.main(["plan", "--max-runtime", "7"])
        finally:
            cli.cmd_sync, watchdog.arm = orig
        self.assertEqual(seen, [7.0])

    def test_watch_survives_one_deadline(self):
        marker = self.state / "hang-once"
        self.state.mkdir(parents=True)
        marker.touch()
        p = self.spawn(["watch", "--interval", "1", "--max-runtime", "2"], hang=str(marker))
        t0 = time.monotonic()
        try:
            while not list(self.state.glob("base-*")) and time.monotonic() - t0 < 25:
                self.assertIsNone(p.poll(), "watch must keep running after a deadline")
                time.sleep(0.2)
        finally:
            p.kill()
            p.communicate()
        self.assertFalse(marker.exists())
        self.assertIn("deadline of 2 s exceeded", self.history())
        self.assertTrue(list(self.state.glob("base-*")), "the second run should complete")
