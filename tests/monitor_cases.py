"""MonitorTests: `docsync monitor` and the origin line of every sync/plan report. Collected through
test_sync_local, which re-exports the class; no test_ prefix, so discovery does not run it twice.

launchd state comes from the test hook DOCSYNC_TEST_LAUNCHCTL: a file holding what
`launchctl print gui/<uid>/com.a321.docsync` would print (missing file = not loaded). Peers are
second state dirs on this Mac reached through tests/fake_ssh.py; their `docsync` is a wrapper on PATH
that points the hooks at the peer's own state dir and runs this checkout's docsync."""
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from docsync import api
from docsync.config import Config, Peer, Root, save_config

ROOT = Path(__file__).resolve().parent.parent
FAKE_SSH = str(Path(__file__).resolve().parent / "fake_ssh.py")
# The fake peer's docsync, only in $HOME/.local/bin: the probe must find it by extending PATH.
WRAPPER = """#!/bin/sh
[ -e "$DOCSYNC_STATE_DIR/hang" ] && sleep 30
if [ -e "$DOCSYNC_STATE_DIR/old" ]; then
  [ "$1" = --version ] && {{ echo "docsync 1.3.2"; exit 0; }}
  echo "usage: docsync [-h] [--version] {{init,peers,status,sync}} ..." >&2
  echo "docsync: error: argument cmd: invalid choice: 'monitor' (choose from 'init', 'sync')" >&2
  exit 2
fi
DOCSYNC_TEST_LAUNCHCTL="$DOCSYNC_STATE_DIR/launchctl.txt" DOCSYNC_CONFIG_DIR="$DOCSYNC_STATE_DIR/cfg" \\
DOCSYNC_LOG_DIR="$DOCSYNC_STATE_DIR/logs" DOCSYNC_TEST_VERSION=$(cat "$DOCSYNC_STATE_DIR/version" 2>/dev/null || echo 1.3.3) \\
PYTHONPATH={root} exec {python} -m docsync "$@"
"""
# A real CLI sync in a subprocess; the peer is a local tree, so resolve() is stubbed (as in deadline_cases).
WRAP = ("import sys; from docsync import cli; cli.resolve = lambda p, log: (p.hosts[0], 'LAN'); "
        "raise SystemExit(cli.main(sys.argv[1:]))")
STAMP = "%Y-%m-%d %H:%M:%S"
# The agent's interpreter in these tests: not the monitor's python, answers `-m docsync --version`
# with the version in the file next to it.
AGENT_PY = """#!/bin/sh
[ "$*" = "-m docsync --version" ] && {{ echo "docsync $(cat {dir}/version)"; exit 0; }}
exit 1
"""
_AGENT = [""]  # path of this test's fake agent interpreter (set in setUp)


def launchctl_text(args=None, state="not running", last_exit="0", interval=900, plist="/x/com.a321.docsync.plist") -> str:
    args = args or [_AGENT[0], "-m", "docsync", "sync", "--all", "--quiet", "--from-agent", "--max-runtime", "1800"]
    inner = "".join(f"\t\t{a}\n" for a in args)
    return (f"gui/502/com.a321.docsync = {{\n\tactive count = 0\n\tpath = {plist}\n"
            f"\tstate = {state}\n\n\tprogram = {args[0]}\n\targuments = {{\n{inner}\t}}\n\n"
            f"\tresource coalition = {{\n\t\tstate = active\n\t}}\n\trun interval = {interval} seconds\n"
            f"\tlast exit code = {last_exit}\n}}\n")


def block(age: float, peers="2 ok, 0 failed", dry=False, agent="yes", body="", files=3, conflicts=0) -> str:
    stamp = time.strftime(STAMP, time.localtime(time.time() - age))
    origin = "" if agent is None else f"origin: /x/docsync sync --all | parent 1 /sbin/launchd | agent={agent}\n"
    tag = " (DRY RUN)" if dry else ""
    return (f"\n=== {stamp}\n{origin}── node-a @ host via LAN{' (DRY RUN — nothing written)' if dry else ''}\n{body}"
            f"══ TOTAL{tag}\n   peers        {peers}\n   files        {files} {'planned' if dry else 'transferred'}  (1 KB)\n"
            f"   deleted      0    conflicts {conflicts}\n   time         1.0s    throughput 1 KB/s\n")


RUN = {"time": "2026-11-08 12:00:00", "age_s": 600, "kind": "report", "ok": 2, "failed": 0, "busy": 0,
       "files": 1, "conflicts": 0, "peer_lock_lost": 0, "symlink_skipped": 0, "held_deletes": 0, "failed_peers": [],
       "deadline_exceeded": False, "origin": {"text": "docsync sync | parent 1 launchd | agent=yes", "agent": True,
                                                                                      "by": "agent"}}


def healthy(**kw) -> dict:
    """Facts of a healthy node (one `nodes[]` entry); kw replaces keys, dict values are merged."""
    n = {"node": "n", "state": "ok", "version": "1.3.3", "running": {"run_lock": None, "root_locks": []},
         "agent": {"loaded": True, "state": "not running", "last_exit": 0, "program": ["/py", "-m", "docsync"],
                   "program_ok": True, "interval_s": 900, "max_runtime": 1800, "loaded_age_s": 86400},
         "last_run": dict(RUN), "last_success": dict(RUN), "last_synced": dict(RUN), "last_agent_run": dict(RUN), "conflicts": {"documents": 0},
         "deadline_dumps": [], "icloud": {"checked_at": 0, "age_s": 60, "roots": {"documents": True}}, "alerts": []}
    for k, v in kw.items():
        n[k] = {**n[k], **v} if isinstance(v, dict) and isinstance(n.get(k), dict) else v
    return n


class MonitorBase(unittest.TestCase):
    """Temp state/log/Documents dirs, the fake agent interpreter and the peer wrapper; no tests."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.state, self.logs, self.docs = self.base / "state", self.base / "logs", self.base / "Documents"
        for d in (self.state, self.logs, self.docs):
            d.mkdir()
        self.home = self.base / "home"
        bindir = self.home / ".local" / "bin"
        bindir.mkdir(parents=True)
        (bindir / "docsync").write_text(WRAPPER.format(root=ROOT, python=sys.executable))
        (bindir / "docsync").chmod(0o755)
        (self.base / "icloud-hook.json").write_text(json.dumps({"node": "node-c"}))
        from docsync import __version__
        agent = self.base / "agent-venv"  # stands in for maccluster's venv python
        agent.mkdir()
        (agent / "version").write_text(__version__)
        (agent / "python").write_text(AGENT_PY.format(dir=agent))
        (agent / "python").chmod(0o755)
        self.agent_py = _AGENT[0] = str(agent / "python")
        env = {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "HOME": str(self.home),  # like a non-interactive ssh
               "DOCSYNC_TEST_LAUNCHCTL": str(self.state / "launchctl.txt"),
               "DOCSYNC_TEST_ICLOUD": str(self.base / "icloud-hook.json")}
        self.env = mock.patch.dict(os.environ, env)
        self.env.start()
        self.cfg = Config(path=str(self.docs), trash=False, roots=[Root("documents", str(self.docs))])
        from docsync import monitor
        monitor._SCAN.update(thread=None, box=None, last=None)  # a late scan of an earlier test is not ours

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def node(self, history: str = "", launchctl: str | None = None, state: Path | None = None):
        from docsync import monitor
        state = state or self.state
        (state / "history.log").write_text(history)
        f = state / "launchctl.txt"
        if launchctl is None:
            f.unlink(missing_ok=True)
        else:
            f.write_text(launchctl)
        return monitor.local_node(self.cfg, state_dir=state, log_dir=self.logs)

    def alerts(self, node) -> list[tuple[str, str]]:
        from docsync import monitor_alerts
        return [(a.level, a.text) for a in monitor_alerts.alerts(node)]

    def peer_state(self, name: str) -> Path:
        d = self.base / name
        (d / "logs").mkdir(parents=True)
        return d

    def spawn(self, args, cfgdir: Path, **env):
        e = dict(os.environ, PYTHONPATH=str(ROOT), DOCSYNC_CONFIG_DIR=str(cfgdir), DOCSYNC_STATE_DIR=str(self.state),
                 DOCSYNC_LOG_DIR=str(self.logs), **env)
        return subprocess.Popen([sys.executable, "-c", WRAP, *args], env=e, text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)


class MonitorTests(MonitorBase):
    def test_history_last_run_and_success(self):
        from docsync import monitor
        lost = "   FAILED after 1.0s: peer lock lost (sleep or link down) — base not written\n"
        sym = "   ⚠ skipped (symlink on node-c): 32 path(s)\n"
        text = (block(5 * 3600, agent=None)                                   # an old success without origin
                + block(3 * 3600, files=7)                                     # the last success
                + block(2 * 3600, peers="1 ok, 1 failed (node-b)", agent="no", body=lost + sym + sym, conflicts=2)
                + block(3600, dry=True)                                        # plan: ignored
                + block(1800, peers="0 ok, 0 failed, 2 busy (node-a, node-b)"))  # all busy: ignored
        h = monitor.parse_history(text)
        run, ok = h["last_run"], h["last_success"]
        self.assertEqual((run["ok"], run["failed"], run["busy"], run["conflicts"]), (1, 1, 0, 2))
        self.assertEqual((run["peer_lock_lost"], run["symlink_skipped"], run["deadline_exceeded"]), (1, 2, False))
        self.assertEqual(run["origin"]["agent"], False)
        self.assertIn("parent 1 /sbin/launchd", run["origin"]["text"])
        self.assertAlmostEqual(run["age_s"], 2 * 3600, delta=5)
        self.assertEqual(ok["files"], 7)
        self.assertAlmostEqual(ok["age_s"], 3 * 3600, delta=5)
        self.assertEqual(monitor.parse_history(block(60, agent=None))["last_run"]["origin"], None)
        # a deadline event is a run of its own (no report block), with its origin
        stamp = time.strftime(STAMP, time.localtime(time.time() - 60))
        dl = f"\n=== {stamp} deadline of 1800 s exceeded — thread stacks in /x/deadline.txt\norigin: docsync sync | parent ? | agent=yes\n"
        h = monitor.parse_history(text + dl)
        self.assertTrue(h["last_run"]["deadline_exceeded"])
        self.assertEqual(h["last_run"]["origin"]["agent"], True)
        self.assertEqual(h["last_success"]["files"], 7)
        h = monitor.parse_history(block(60, dry=True) + block(30, peers="0 ok, 1 failed (b)"))
        self.assertEqual((h["last_run"]["failed"], h["last_success"]), (1, None))
        self.assertEqual(monitor.parse_history(""), {"last_run": None, "last_success": None, "last_synced": None,
                                                     "last_agent_run": None})

    def test_alert_levels(self):
        good = block(600)
        node = self.node(good, launchctl_text())
        before = {p.name for p in self.state.iterdir()}
        self.assertEqual(self.alerts(node), [])
        self.assertEqual((node["agent"]["loaded"], node["agent"]["program_ok"], node["agent"]["interval_s"],
                          node["agent"]["max_runtime"], node["agent"]["foreign_reason"]), (True, True, 900, 1800, None))
        from docsync import __version__
        self.assertNotEqual(self.agent_py, sys.executable)  # the monitor runs under another python: still own
        self.assertEqual(node["agent"]["version"], __version__)
        (Path(self.agent_py).parent / "version").write_text("9.0.0")  # newer than the monitor: own
        self.assertEqual(self.alerts(self.node(good, launchctl_text())), [])
        (Path(self.agent_py).parent / "version").write_text("1.2.9")
        cases = [
            (dict(launchctl=None), ("FAIL", "agent not loaded")),
            (dict(launchctl=launchctl_text(last_exit="2")), ("FAIL", "agent last exit 2")),
            (dict(launchctl=launchctl_text(args=["/opt/py3.15", "/x/bin/docsync-launchd.py", "sync", "--max-runtime", "1800"])),
             ("FAIL", "foreign program /opt/py3.15 /x/bin/docsync-launchd.py (shim)")),
            (dict(launchctl=launchctl_text(args=["/nonexistent/python", "-m", "docsync", "sync", "--max-runtime", "1800"])),
             ("FAIL", "foreign program /nonexistent/python -m docsync (missing interpreter)")),
            (dict(history=block(3 * 3600)), ("FAIL", "last success 3h 00m ago")),
            (dict(history=good + block(60, agent="no", peers="0 ok, 0 failed, 1 busy (b)") + block(30, agent="no")),
             ("WARN", "last run did not come from the agent: /x/docsync sync --all | parent 1 /sbin/launchd | agent=no")),
            (dict(history=good + block(60, peers="1 ok, 1 failed (b)",
                                       body="   FAILED after 1s: peer lock lost (sleep or link down) — base not written\n")),
             [("WARN", "1 peer run(s) failed in the last run: node-a"), ("WARN", "peer lock lost in the last run (1)")]),
        ]
        node = self.node(good, launchctl_text())
        self.assertEqual(self.alerts(node), [("FAIL", f"foreign program {self.agent_py} -m docsync (old 1.2.9)")])
        self.assertEqual(node["agent"]["foreign_reason"], "old 1.2.9")
        from docsync import __version__
        (Path(self.agent_py).parent / "version").write_text(__version__)
        for kw, want in cases:
            kw = {"history": good, "launchctl": launchctl_text(), **kw}
            self.assertEqual(self.alerts(self.node(**kw)), want if isinstance(want, list) else [want], kw)
        self.assertEqual(self.alerts(self.node(good, launchctl_text(last_exit="3"))), [])  # busy is fine
        self.assertEqual(self.alerts(self.node(good, launchctl_text(last_exit="(never exited)"))), [])
        (self.docs / "a.conflict-node-b-20261108.txt").write_text("x")
        (self.docs / ".docsync-trash").mkdir()
        (self.docs / ".docsync-trash" / "b.conflict-x.txt").write_text("x")  # filtered like `docsync conflicts`
        node = self.node(good, launchctl_text())
        self.assertEqual(node["conflicts"], {"documents": 1})
        self.assertEqual(self.alerts(node), [("WARN", "1 open conflict copy")])
        for p in self.docs.rglob("*.conflict-*"):
            p.unlink()
        (self.logs / "deadline-20261108T120000Z.txt").write_text("stacks")
        old = self.logs / "deadline-20261101T120000Z.txt"
        old.write_text("stacks")
        os.utime(old, (time.time() - 3 * 86400,) * 2)
        node = self.node(good, launchctl_text())
        self.assertEqual(node["deadline_dumps"], [str(self.logs / "deadline-20261108T120000Z.txt")])
        self.assertEqual(self.alerts(node), [("WARN", "deadline dump within 24 h: deadline-20261108T120000Z.txt")])
        (self.logs / "deadline-20261108T120000Z.txt").unlink()
        # running now: the run lock of a live process, a dead one is not running
        (self.state / "run.lock").write_text(f"{os.getpid()}\n{time.time() - 120:.3f}\ndocsync sync --all\n")
        node = self.node(good, launchctl_text())
        self.assertTrue(node["running"]["run_lock"]["alive"])
        self.assertEqual(node["running"]["run_lock"]["command"], "docsync sync --all")
        (self.state / "run.lock").write_text(f"99999999\n{time.time():.3f}\ndocsync sync\n")
        self.assertFalse(self.node(good, launchctl_text())["running"]["run_lock"]["alive"])
        (self.state / "icloud-node-c.json").write_text(json.dumps(
            {"checked_at": time.time() - 3600, "node": "node-c", "roots": {"documents": {"ok": True, "dd": True}}}))
        node = self.node(good, launchctl_text())
        self.assertEqual(node["icloud"]["roots"], {"documents": True})
        self.assertAlmostEqual(node["icloud"]["age_s"], 3600, delta=5)
        after = {p.name for p in self.state.iterdir()}
        self.assertEqual(after - before, {"run.lock", "icloud-node-c.json"})  # written by the test only
        self.assertFalse((self.state / "locks").exists())

    def test_peer_rows_via_fake_ssh(self):
        from docsync import monitor_report
        a, b = self.peer_state("node-a"), self.peer_state("node-b")
        (a / "history.log").write_text(block(600))
        (a / "launchctl.txt").write_text(launchctl_text(last_exit="2"))
        (b / "old").touch()  # docsync 1.3.2 on node-b: no monitor
        self.node(block(600), launchctl_text())
        specs = [api.PeerSpec("node-a", "fake-a", hosts_tried=["fake-a"], paths={"documents": str(self.docs)}),
                 api.PeerSpec("node-b", "fake-b", hosts_tried=["fake-b"], paths={"documents": str(self.docs)}),
                 api.PeerSpec("node-d", None, hosts_tried=["10.42.0.9"])]
        dirs = {"node-a": a, "node-b": b}
        rep = monitor_report.gather(self.cfg, specs, state_dir=self.state, log_dir=self.logs,
                                    ssh_cmd=lambda n: [sys.executable, FAKE_SSH, "--state", str(dirs[n])])
        nodes = {n["node"]: n for n in rep["nodes"]}
        self.assertEqual(list(nodes), ["node-c", "node-a", "node-b", "node-d"])
        self.assertEqual(nodes["node-a"]["state"], "ok")
        self.assertEqual(nodes["node-a"]["agent"]["last_exit"], 2)  # computed on the peer
        self.assertEqual(nodes["node-a"]["last_success"]["files"], 3)
        self.assertEqual(nodes["node-b"]["state"], "docsync too old on node-b (needs 1.3.3)")
        self.assertEqual(nodes["node-d"]["state"], "unreachable")
        self.assertEqual(rep["exit_code"], 1)
        self.assertIn({"node": "node-a", "level": "FAIL", "code": "agent_exit", "text": "agent last exit 2",
                       "data": {"exit": 2}}, rep["alerts"])
        self.assertIn({"node": "node-b", "level": "WARN", "code": "peer_too_old",
                       "text": "docsync too old on node-b (needs 1.3.3)", "data": {"version": "1.3.2"}}, rep["alerts"])
        self.assertIn({"node": "node-d", "level": "WARN", "code": "peer_unreachable", "text": "unreachable",
                       "data": {"state": "unreachable"}}, rep["alerts"])
        out = monitor_report.render(rep)
        self.assertIn("docsync too old on node-b (needs 1.3.3)", out)
        self.assertIn("node-d", out)
        self.assertIn("FAIL node-a: agent last exit 2", out)
        (a / "launchctl.txt").write_text(launchctl_text())
        rep = monitor_report.gather(self.cfg, specs[:1], state_dir=self.state, log_dir=self.logs,
                                    ssh_cmd=lambda n: [sys.executable, FAKE_SSH, "--state", str(dirs[n])])
        self.assertEqual((rep["exit_code"], rep["alerts"]), (0, []), monitor_report.render(rep))

    def test_origin_line_written_by_real_cli_sync(self):
        from docsync import monitor
        peer = self.base / "B" / "Documents"
        peer.mkdir(parents=True)
        (self.docs / "x.txt").write_text("x")
        cfgdir = self.base / "cfg"
        save_config(Config(path=str(self.docs), trash=False, peers=[Peer("b", [str(peer)])]), cfgdir / "config.toml")
        for args, agent in ((["sync", "--all", "--quiet", "--no-notify"], "no"),
                            (["plan", "--quiet"], "no"),
                            (["sync", "--all", "--quiet", "--no-notify", "--from-heal"], "heal"),
                            (["sync", "--all", "--quiet", "--no-notify", "--from-agent"], "yes")):
            p = self.spawn(args, cfgdir)
            out, err = p.communicate(timeout=60)
            self.assertEqual(p.returncode, 0, err)
            last = (self.state / "history.log").read_text().rsplit("\n=== ", 1)[1]
            origin = [ln for ln in last.splitlines() if ln.startswith("origin: ")]
            self.assertEqual(len(origin), 1, last)
            self.assertIn(f" {args[0]} ", origin[0] + " ")
            self.assertIn(f" | parent {os.getpid()} ", origin[0])  # this test process started the run
            self.assertTrue(origin[0].endswith(f" | agent={agent}"), origin[0])
        self.assertTrue((peer / "x.txt").exists())
        h = monitor.parse_history((self.state / "history.log").read_text())
        self.assertEqual((h["last_run"]["origin"]["agent"], h["last_success"]["ok"]), (True, 1))
        with mock.patch.object(monitor.subprocess, "run", side_effect=OSError("no ps")):
            self.assertIn(" | parent ? | agent=no", monitor.origin_line(from_agent=False))
        line = monitor.origin_line(argv=["docsync", "sync\nx"], ppid=os.getpid(), from_agent=True)
        self.assertEqual(line.count("\n"), 0)
        self.assertLessEqual(len(line.split(" | parent ", 1)[1].split(" | agent=")[0]), 200 + 12)

    def test_json_schema_keys(self):
        cfgdir = self.base / "cfg"
        save_config(Config(path=str(self.docs), trash=False), cfgdir / "config.toml")
        (self.state / "history.log").write_text(block(600))
        (self.state / "launchctl.txt").write_text(launchctl_text())
        p = self.spawn(["monitor", "--local", "--json"], cfgdir)
        out, err = p.communicate(timeout=60)
        self.assertEqual(p.returncode, 0, err + out)
        rep = json.loads(out.strip().splitlines()[-1])
        self.assertEqual(set(rep), {"schema", "version", "checked_at", "ok", "exit_code", "nodes", "alerts"})
        self.assertEqual(rep["schema"], 1)
        node = rep["nodes"][0]
        self.assertEqual(set(node), {"node", "state", "version", "agent", "running", "last_run", "last_success", "last_synced", "last_agent_run",
                                     "conflicts", "deadline_dumps", "icloud", "alerts"})
        self.assertEqual(set(node["agent"]), {"loaded", "state", "last_exit", "program", "program_ok", "interval_s",
                                              "max_runtime", "version", "foreign_reason", "loaded_age_s"})
        self.assertEqual(set(node["running"]), {"run_lock", "root_locks"})
        self.assertEqual(set(node["last_run"]), {"time", "age_s", "kind", "ok", "failed", "busy", "files", "conflicts",
                                                 "peer_lock_lost", "symlink_skipped", "held_deletes", "failed_peers", "deadline_exceeded",
                                                 "origin"})
        self.assertEqual(set(node["last_success"]), set(node["last_run"]))
        self.assertEqual(set(node["last_synced"]), set(node["last_run"]))
        self.assertEqual(set(node["last_run"]["origin"]), {"text", "agent", "by"})
        (self.state / "launchctl.txt").unlink()
        p = self.spawn(["monitor", "--local", "--json"], cfgdir)
        out, _ = p.communicate(timeout=60)
        rep = json.loads(out.strip().splitlines()[-1])
        self.assertEqual(rep["alerts"], [{"node": "node-c", "level": "FAIL", "code": "agent_not_loaded",
                                          "text": "agent not loaded", "data": {}}])
        self.assertEqual(rep["nodes"][0]["alerts"], [{k: v for k, v in rep["alerts"][0].items() if k != "node"}])
        p = self.spawn(["monitor", "--local"], cfgdir)
        out, err = p.communicate(timeout=60)
        self.assertEqual(p.returncode, 1, err + out)
        self.assertIn("FAIL node-c: agent not loaded", out)
        p = self.spawn(["monitor", "--local", "--interval", "0.2"], cfgdir)
        time.sleep(1.5)
        p.send_signal(signal.SIGINT)
        out, err = p.communicate(timeout=20)
        self.assertEqual((p.returncode, err), (130, ""))
        self.assertGreaterEqual(out.count("FAIL node-c: agent not loaded"), 2, out)

    def test_alert_codes(self):
        """monitor_alerts.alerts() on hand-made facts: one case per code, each alone on a healthy node."""
        from docsync.monitor_alerts import alerts
        self.assertEqual(alerts(healthy()), [])
        origin = "docsync sync | parent 9 zsh | agent=no"
        cases = [
            (healthy(agent={"loaded": False}), "FAIL", "agent_not_loaded", {}),
            (healthy(agent={"last_exit": 4}), "FAIL", "agent_exit", {"exit": 4}),
            (healthy(agent={"program_ok": False, "program": ["/opt/py3.15", "x.py"], "foreign_reason": "shim"}),
             "FAIL", "agent_foreign", {"program": ["/opt/py3.15", "x.py"], "reason": "shim"}),
            (healthy(agent={"max_runtime": None}), "WARN", "agent_no_max_runtime", {}),
            (healthy(agent={"max_runtime": 0}), "WARN", "agent_no_max_runtime", {}),
            (healthy(last_synced={"age_s": 3 * 3600 + 59}), "FAIL", "stale_success", {"age_min": 180}),
            (healthy(last_synced=None, last_success=None), "FAIL", "stale_success", {"age_min": None}),
            (healthy(last_synced=None, last_success=None, agent={"loaded_age_s": 600}), "WARN", "never_synced",
             {"loaded_age_min": 10, "grace_min": 60}),
            (healthy(last_run={"failed": 1, "ok": 1, "failed_peers": [{"peer": "b", "root": "", "reason": "x"}]},
                     last_agent_run={"failed": 1}, agent={"last_exit": 2}), "WARN", "peer_failed",
             {"peers": ["b"], "reasons": ["b: x"]}),
            (healthy(agent={"last_exit": 2}), "FAIL", "agent_exit", {"exit": 2}),
            (healthy(conflicts={"documents": 2, "desktop": 1}), "WARN", "conflicts",
             {"count": 3, "roots": {"documents": 2, "desktop": 1}}),
            (healthy(deadline_dumps=["/l/deadline-1.txt"]), "WARN", "deadline_dump", {"paths": ["/l/deadline-1.txt"]}),
            (healthy(last_run={"peer_lock_lost": 1}), "WARN", "peer_lock_lost", {"count": 1}),
            (healthy(last_run={"origin": {"text": origin, "agent": False, "by": "other"}}), "WARN", "non_agent_run",
             {"origin": origin}),
            (healthy(state="unreachable"), "WARN", "peer_unreachable", {"state": "unreachable"}),
            (healthy(state="error: rc=1"), "WARN", "peer_unreachable", {"state": "error: rc=1"}),
            (healthy(state="docsync too old on b (needs 1.3.3)", version="1.3.2"), "WARN", "peer_too_old", {"version": "1.3.2"}),
            (healthy(last_run={"held_deletes": 7}), "WARN", "held_deletes", {"count": 7}),
            (healthy(icloud={"roots": {"documents": False, "desktop": True}}), "WARN", "icloud_incomplete",
             {"roots": ["documents"]}),
        ]
        for node, level, code, data in cases:
            got = alerts(node)
            self.assertEqual([(a.level, a.code, a.data) for a in got], [(level, code, data)], code)
            self.assertEqual(set(got[0].to_dict("n")), {"node", "level", "code", "text", "data"})
        self.assertEqual(len({c for _, _, c, _ in cases}), 15)  # every code of the heal contract
        self.assertEqual(alerts(healthy(agent={"last_exit": 3})), [])  # busy is not a failure
        heal = {"text": "docsync sync --from-heal | parent 9 python | agent=heal", "agent": False, "by": "heal"}
        self.assertEqual(alerts(healthy(last_run={"origin": heal})), [])  # heal's own syncs are expected

    def test_held_deletes_parsed(self):
        from docsync import monitor
        held = "   deleted      local 0  remote 0  (⚠ 1,204 held back by deletion guard)\n"
        h = monitor.parse_history(block(60, body=held + held.replace("1,204", "3")))
        self.assertEqual(h["last_run"]["held_deletes"], 1207)


try:  # MonitorHealthTests reuses MonitorBase; re-exported here so test_sync_local needs one line
    from tests.monitor_health_cases import MonitorHealthTests as MonitorHealthTests
except ImportError:
    from monitor_health_cases import MonitorHealthTests as MonitorHealthTests
