"""PeerLockTests (per-root lock on both sides of a peer pair) and DeadlineStackTests. Collected through
test_sync_local, which re-exports both classes; no test_ prefix, so discovery does not run them twice.
Remote peers go through tests/fake_ssh.py: the "peer" is a second tree and state dir on this Mac and
its helper runs under /usr/bin/python3 (3.9), never over the network."""
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from docsync.config import Config

ROOT = Path(__file__).resolve().parent.parent
FAKE_SSH = str(Path(__file__).resolve().parent / "fake_ssh.py")
ENV = dict(os.environ, PYTHONPATH=str(ROOT))

HOLD_ROOT = ("import os, sys, time; from pathlib import Path; from docsync import lock; "
             "fh = lock.acquire_root(Path(sys.argv[1]), Path(sys.argv[2])); "
             "rec = lock.record_fields(); rec['host'] = sys.argv[3] or rec['host']; "
             "fh and (fh.truncate(0), os.pwrite(fh.fileno(), lock.encode_record(rec), 0)); "
             "print('held' if fh else 'busy', flush=True); time.sleep(60)")

# One side of a reverse pair: local tree + state, peer tree + state; starts at a shared instant.
# The log hook sleeps after the plan, while both root locks are held, so the two runs overlap.
REVERSE_RUN = """
import sys, time
from pathlib import Path
from docsync import session
from docsync.config import Config
from docsync.transport import RemoteTransport
lroot, lstate, proot, pstate, fake, at = sys.argv[1:7]
t = RemoteTransport("fakepeer", proot)
t.ssh_cmd = t.bulk_ssh_cmd = [sys.executable, fake, "--state", pstate]
def log(*a):
    if "plan:" in " ".join(map(str, a)):
        time.sleep(2)
while time.time() < float(at):
    time.sleep(0.005)
rep = session.run_many(Config(path=lroot, trash=False), [("peer", t)], state_dir=Path(lstate), log=log)[0]
print(rep.render())
print("TRANSFERRED", rep.transferred_files)
"""

# Takes the peer root lock through the helper with heartbeats, then waits to be killed.
HOLD_PEER = """
import sys, time
from docsync.transport import RemoteTransport
t = RemoteTransport("fakepeer", sys.argv[1])
t.ssh_cmd = [sys.executable, sys.argv[3], "--state", sys.argv[2]]
pl = t.root_lock()
print("locked" if pl.lock(1.0) is None else "busy", flush=True)
pl.keep_alive()
time.sleep(3)  # 3 heartbeats of 1 s: the acks keep held() True
print("held", pl.held(), flush=True)
time.sleep(60)
"""


def touch(p: Path, text: str = "x", mtime: int | None = None):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    mtime = mtime or int(time.time()) - 100
    os.utime(p, (mtime, mtime))


def hold_root(state: Path, root: Path, host: str = "") -> subprocess.Popen:
    """A separate process holding the root lock; its record names `host` (default: this Mac)."""
    proc = subprocess.Popen([sys.executable, "-c", HOLD_ROOT, str(state), str(root), host], stdout=subprocess.PIPE,
                            text=True, env=ENV)
    assert proc.stdout.readline().strip() == "held"
    return proc


def stop(proc: subprocess.Popen) -> None:
    if proc.poll() is None:
        proc.kill()
    proc.communicate()


def free_within(state: Path, root: Path, secs: float) -> float | None:
    """Seconds until the root lock could be taken here, or None if it stayed held for `secs`."""
    from docsync import lock
    t0 = time.monotonic()
    while time.monotonic() - t0 < secs:
        fh = lock.acquire_root(state, root)
        if fh is not None:
            fh.close()
            return time.monotonic() - t0
        time.sleep(0.05)
    return None


class PeerLockTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.a, self.b, self.c = (base / n / "Documents" for n in "ABC")
        self.sa, self.sb, self.sc = (base / f"state-{n}" for n in "ABC")
        for d in (self.a, self.b, self.c):
            d.mkdir(parents=True)
        self.cfg = Config(path=str(self.a), trash=False)

    def tearDown(self):
        self.tmp.cleanup()

    def remote(self, root: Path, state: Path):
        from docsync.transport import RemoteTransport
        t = RemoteTransport("fakepeer", str(root))
        t.ssh_cmd = t.bulk_ssh_cmd = [sys.executable, FAKE_SSH, "--state", str(state)]
        return t

    def run_b(self, log=lambda *a: None):
        from docsync import session
        return session.run_many(self.cfg, [("b", self.remote(self.b, self.sb))], state_dir=self.sa, log=log)[0]

    def test_reverse_run_is_busy(self):
        from docsync import lock
        touch(self.a / "x.txt")
        seen = []

        def probe(*a):  # mid-run: both root locks held, the peer's record names this run and host
            if "plan:" in " ".join(map(str, a)):
                seen.append((free_within(self.sa, self.a, 0.3), free_within(self.sb, self.b, 0.3),
                             lock.read_holder(lock.root_lock_path(self.sb, self.b))))
        rep = self.run_b(probe)
        self.assertTrue(rep.ok, rep.error)
        (held_a, held_b, rec), = seen
        self.assertEqual((held_a, held_b), (None, None))
        self.assertEqual((rec["pid"], rec["host"]), (os.getpid(), lock.host_name()))
        self.assertEqual(len(lock.root_lock_path(self.sa, self.a).read_text().splitlines()), 4)
        for f in self.sa.glob("base-*"):
            f.unlink()
        legacy = self.sa / "base-b.json"  # a 0.x base file: renamed only once the pair is locked
        legacy.write_text("{}")
        touch(self.a / "y.txt")
        proc = hold_root(self.sb, self.b)  # "the other run" holds the peer's root lock
        try:
            out = []
            rep = self.run_b(lambda *a: out.append(" ".join(map(str, a))))
        finally:
            stop(proc)
        text = rep.render() + "\n".join(out)
        self.assertIn("peer busy", text)
        self.assertIn(f"pid {proc.pid} on {lock.host_name()}", text)
        self.assertIn("peer busy: another run is in progress (pid", "\n".join(out))
        self.assertEqual(list(self.sa.glob("base-*")) + list(self.sb.glob("base-*")), [legacy])
        self.assertFalse((self.b / "y.txt").exists())

    def test_simultaneous_reverse_runs_one_wins(self):
        touch(self.a / "from-a.txt")
        touch(self.b / "from-b.txt")
        at = str(time.time() + 1.5)
        procs = [subprocess.Popen([sys.executable, "-c", REVERSE_RUN, str(lr), str(ls), str(pr), str(ps), FAKE_SSH, at],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=ENV)
                 for lr, ls, pr, ps in ((self.a, self.sa, self.b, self.sb), (self.b, self.sb, self.a, self.sa))]
        outs = [p.communicate(timeout=60) for p in procs]
        for p, (o, e) in zip(procs, outs):
            self.assertEqual(p.returncode, 0, e)
        moved = sorted(o.strip().splitlines()[-1] for o, _ in outs)
        self.assertEqual(moved, ["TRANSFERRED 0", "TRANSFERRED 2"], outs)
        loser = next(o for o, _ in outs if o.strip().endswith("TRANSFERRED 0"))
        self.assertIn("peer busy", loser)
        self.assertTrue((self.a / "from-b.txt").exists() and (self.b / "from-a.txt").exists())

    def test_busy_peer_is_not_failure(self):
        from docsync import api, hosts
        from docsync.session import base_file
        states = {"host-b": self.sb, "host-c": self.sc}
        real = hosts.transport_for

        def fake(peer, host, path=None, bind=None):
            t = real(peer, host, path, bind)
            t.ssh_cmd = t.bulk_ssh_cmd = [sys.executable, FAKE_SSH, "--state", str(states[host])]
            return t

        def sync():
            peers = [api.PeerSpec("b", "host-b", paths={"documents": str(self.b)}),
                     api.PeerSpec("c", "host-c", paths={"documents": str(self.c)})]
            return api.sync(api.SyncRequest(roots=[api.RootSpec("documents", str(self.a))], peers=peers,
                                            state_dir=self.sa, trash=False))
        hosts.transport_for = fake
        try:
            touch(self.a / "first.txt")
            self.assertEqual(sync().exit_code, 0)
            base_b = base_file("b", self.sa).read_bytes()
            touch(self.a / "second.txt")
            proc = hold_root(self.sb, self.b)
            try:
                res = sync()
                both = hold_root(self.sc, self.c)
                try:
                    res_all = sync()
                finally:
                    stop(both)
            finally:
                stop(proc)
        finally:
            hosts.transport_for = real
        rb, rc = res.report.peers
        self.assertEqual(res.exit_code, 0, res.report.render())
        self.assertTrue(rb.busy and not rc.busy)
        self.assertEqual(rc.pushed_files, 1)
        self.assertEqual(base_file("b", self.sa).read_bytes(), base_b)
        self.assertFalse((self.b / "second.txt").exists())
        self.assertEqual(res.report.notable(), "")
        self.assertIn("peer busy (covered by the other run)", res.report.render())
        self.assertIn("1 busy", res.report.render())
        self.assertEqual(res_all.exit_code, 3, res_all.report.render())
        self.assertEqual(res_all.report.notable(), "")

    def test_silent_holder_releases(self):
        from docsync import lock
        old = os.environ.get("DOCSYNC_LOCK_HEARTBEAT_S")
        os.environ["DOCSYNC_LOCK_HEARTBEAT_S"] = "1"
        try:
            pl = self.remote(self.b, self.sb).root_lock()
            self.assertIn("ServerAliveInterval=10", pl.argv)
            self.assertIn("ControlMaster=no", pl.argv)
            try:
                long_cmd = ("é" * 2500).encode()  # 5000 bytes: the helper cuts the record at 4096
                self.assertIsNone(pl.lock(1.0, b"4242\n1.0\n" + long_cmd + b"\nnode-q\n"))  # no keep_alive(): silent
                raw = lock.root_lock_path(self.sb, self.b).read_bytes()
                self.assertLessEqual(len(raw), 4096)
                raw.decode("utf-8")  # never a UTF-8 sequence cut in half
                self.assertTrue(pl.held())
                self.assertIsNone(free_within(self.sb, self.b, 0.5))
                t = free_within(self.sb, self.b, 4.0)
                self.assertFalse(pl.held())
            finally:
                pl.release()
            self.assertIsNotNone(t, "a silent holder must free the lock within 4 s")
            proc = subprocess.Popen([sys.executable, "-c", HOLD_PEER, str(self.b), str(self.sb), FAKE_SSH],
                                    stdout=subprocess.PIPE, text=True, env=dict(ENV, DOCSYNC_LOCK_HEARTBEAT_S="1"))
            try:
                self.assertEqual(proc.stdout.readline().strip(), "locked")
                self.assertIsNone(free_within(self.sb, self.b, 4.0), "heartbeats keep the lock alive")
                self.assertEqual(proc.stdout.readline().strip(), "held True")
                os.kill(proc.pid, signal.SIGKILL)
                self.assertIsNotNone(free_within(self.sb, self.b, 5.0), "a dead parent must free the lock within 5 s")
            finally:
                stop(proc)
        finally:
            if old is None:
                os.environ.pop("DOCSYNC_LOCK_HEARTBEAT_S", None)
            else:
                os.environ["DOCSYNC_LOCK_HEARTBEAT_S"] = old

    def test_lost_peer_lock_aborts_before_base(self):
        from docsync import lock
        touch(self.a / "x.txt")

        def stall(*a):  # after the plan, before any write: longer than 2 heartbeats without an ack
            if "plan:" in " ".join(map(str, a)):
                time.sleep(4)
        old, real = os.environ.get("DOCSYNC_LOCK_HEARTBEAT_S"), lock.PeerLock.keep_alive
        os.environ["DOCSYNC_LOCK_HEARTBEAT_S"] = "1"
        lock.PeerLock.keep_alive = lambda self: None  # the heartbeat stops (like a sleeping Mac)
        try:
            rep = self.run_b(stall)
        finally:
            lock.PeerLock.keep_alive = real
            if old is None:
                os.environ.pop("DOCSYNC_LOCK_HEARTBEAT_S", None)
            else:
                os.environ["DOCSYNC_LOCK_HEARTBEAT_S"] = old
        self.assertFalse(rep.ok)
        self.assertIn("peer lock lost", rep.error)
        self.assertEqual(list(self.sa.glob("base-*")), [])
        self.assertFalse((self.b / "x.txt").exists())

    def test_local_root_busy_wording(self):
        from docsync import lock
        touch(self.a / "x.txt")
        proc = hold_root(self.sa, self.a, "node-z")  # this Mac's root lock, held by a run of another pair
        try:
            rep = self.run_b()
        finally:
            stop(proc)
        self.assertTrue(rep.busy)
        self.assertIn(f"local root busy (pid {proc.pid} on node-z) — retried next run", rep.render())
        self.assertNotIn("covered by the other run", rep.render())
        self.assertEqual(lock.local_busy_text(None), "local root busy — retried next run")
        self.assertEqual(list(self.sa.glob("base-*")), [])

    def test_cap_follows_request_max_runtime(self):
        from docsync import api, hosts, lock
        argvs, real_for, real_pl = [], hosts.transport_for, lock.PeerLock

        def fake_for(peer, host, path=None, bind=None):
            t = real_for(peer, host, path, bind)
            t.ssh_cmd = t.bulk_ssh_cmd = [sys.executable, FAKE_SSH, "--state", str(self.sb)]
            return t

        def capture(argv, timeout=20.0):  # record the helper command, then sync without a peer lock
            argvs.append(argv)
            raise lock.LockUnavailable("captured")
        hosts.transport_for, lock.PeerLock = fake_for, capture
        try:
            for kw in ({"max_runtime": 3600}, {}, {"max_runtime": 0}):
                res = api.sync(api.SyncRequest(roots=[api.RootSpec("documents", str(self.a))], state_dir=self.sa,
                                               peers=[api.PeerSpec("b", "host-b", paths={"documents": str(self.b)})],
                                               trash=False, **kw))
                self.assertEqual(res.exit_code, 0, res.report.render())
        finally:
            hosts.transport_for, lock.PeerLock = real_for, real_pl
        self.assertEqual([a[-1].split()[-1] for a in argvs], ["3660", "1860", "0"])

    def test_missing_local_root_takes_no_lock(self):
        from docsync import session
        from docsync.transport import LocalTransport
        rep = session.run_many(self.cfg, [("b", LocalTransport(self.b.parent / "missing"))],
                               state_dir=self.sa, log=lambda *a: None)[0]
        self.assertFalse(rep.ok)
        self.assertIn("not a directory", rep.error)
        self.assertEqual(list(self.sa.glob("locks/*")), [])

    def test_old_holder_record(self):
        from docsync import lock
        path = lock.root_lock_path(self.sa, self.a)
        path.parent.mkdir(parents=True)
        path.write_text(f"4242\n{time.time():.3f}\nold docsync sync\n")
        self.assertNotIn("host", lock.read_holder(path))
        self.assertEqual(lock.root_busy_message(self.sa, self.a),
                         "another run is in progress (pid 4242, running 0 min, old docsync sync)")
        path.write_text(f"4242\n{time.time():.3f}\nnew docsync sync\nnode-q\n")
        self.assertEqual(lock.root_busy_message(self.sa, self.a),
                         "another run is in progress (pid 4242 on node-q, running 0 min, new docsync sync)")


try:  # the module, not the class: a module-level DeadlineTests would be collected here a second time
    from tests import deadline_cases
except ImportError:
    import deadline_cases


class DeadlineStackTests(unittest.TestCase):
    _d = deadline_cases.DeadlineTests
    setUp, tearDown, spawn, history = _d.setUp, _d.tearDown, _d.spawn, _d.history

    def test_deadline_writes_stacks(self):
        p = self.spawn(["sync", "--all", "--quiet", "--no-notify", "--max-runtime", "2"], hang="1")
        _, err = p.communicate(timeout=30)
        self.assertEqual(p.returncode, 4, err)
        dumps = list(self.logs.glob("deadline-*.txt"))
        self.assertEqual(len(dumps), 1, err)
        text = dumps[0].read_text()
        self.assertIn("Thread", text)
        self.assertIn("transport.py", text)
        self.assertIn(str(dumps[0]), self.history())
