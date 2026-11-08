"""MonitorHealthTests: no false FAIL from `docsync monitor` (heal acts on FAILs). Re-exported through
monitor_cases and test_sync_local; no test_ prefix, so discovery does not run it twice."""
import os
import sys
import time
from pathlib import Path
from unittest import mock

from docsync import api

try:
    from tests.monitor_cases import FAKE_SSH, MonitorBase, block, launchctl_text
except ImportError:
    from monitor_cases import FAKE_SSH, MonitorBase, block, launchctl_text


class MonitorHealthTests(MonitorBase):
    def codes(self, node) -> list[tuple[str, str]]:
        from docsync import monitor_alerts
        return [(a.level, a.code) for a in monitor_alerts.alerts(node)]

    def test_exit2_with_failed_peer_is_no_fail(self):
        from docsync import monitor
        peer = self.base / "peer-a"
        peer.mkdir()
        res = api.sync(api.SyncRequest(roots=[api.RootSpec("documents", str(self.docs))],
                                       peers=[api.PeerSpec("node-a", str(peer)),
                                              api.PeerSpec("node-b", None, hosts_tried=["10.42.0.2"])],
                                       state_dir=self.base / "sync-state", safetynet=False, trash=False))
        self.assertEqual(res.exit_code, 2)  # the agent's exit with one unreachable peer
        stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time() - 600))
        hist = block(5 * 3600) + f"\n=== {stamp}\norigin: x | parent 1 launchd | agent=yes\n{res.report.render()}\n"
        run = monitor.parse_history(hist)["last_run"]
        self.assertEqual((run["ok"], run["failed"], [f["peer"] for f in run["failed_peers"]]), (1, 1, ["node-b"]))
        node = self.node(hist, launchctl_text(last_exit="2"))
        self.assertEqual(self.codes(node), [("WARN", "peer_failed")])  # neither agent_exit nor stale_success
        from docsync import monitor_alerts
        data = monitor_alerts.alerts(node)[0].data
        self.assertEqual(data["peers"], ["node-b"])
        self.assertIn("node-b [documents]: none of 10.42.0.2", data["reasons"][0])
        self.assertAlmostEqual(node["last_success"]["age_s"], 5 * 3600, delta=5)  # strict rule: failed == 0
        self.assertAlmostEqual(node["last_synced"]["age_s"], 600, delta=5)

    def test_empty_history_never_synced_then_stale(self):
        plist = self.base / "com.a321.docsync.plist"
        plist.write_text("<plist/>")
        node = self.node("", launchctl_text(plist=str(plist)))
        self.assertEqual(self.codes(node), [("WARN", "never_synced")])
        self.assertLess(node["agent"]["loaded_age_s"], 60)
        old = time.time() - (2 * 900 + 1800 + 60)  # past the grace of 2 × interval + 30 min
        os.utime(plist, (old, old))
        self.assertEqual(self.codes(self.node("", launchctl_text(plist=str(plist)))), [("FAIL", "stale_success")])
        self.assertEqual(self.codes(self.node(block(600), launchctl_text(plist=str(plist)))), [])

    def test_busy_counts_as_success_and_origin_is_not_parsed(self):
        from docsync import monitor
        h = monitor.parse_history(block(60, peers="6 ok, 0 failed, 4 busy (a, b, c, d)"))
        self.assertEqual((h["last_success"]["ok"], h["last_success"]["busy"]), (6, 4))
        tricky = block(60, peers="0 ok, 2 failed (a, b)").replace("agent=yes", "1 ok, 0 failed | agent=yes")
        h = monitor.parse_history(tricky)
        self.assertEqual((h["last_success"], h["last_synced"], h["last_run"]["failed"]), (None, None, 2))

    def peer_spec(self, name: str) -> api.PeerSpec:
        return api.PeerSpec(name, f"fake-{name}", hosts_tried=[f"fake-{name}"], paths={"documents": str(self.docs)})

    def gather(self, specs, dirs):
        from docsync import monitor_report
        return monitor_report.gather(self.cfg, specs, state_dir=self.state, log_dir=self.logs,
                                     ssh_cmd=lambda n: [sys.executable, FAKE_SSH, "--state", str(dirs[n])])

    def test_probe_timeout_is_bounded(self):
        from docsync import monitor_report
        a = self.peer_state("node-a")
        (a / "hang").touch()
        self.node(block(600), launchctl_text())
        t0 = time.monotonic()
        with mock.patch.object(monitor_report, "PROBE_TIMEOUT", 1):
            rep = self.gather([self.peer_spec("node-a")], {"node-a": a})
        self.assertLess(time.monotonic() - t0, 10)
        node = rep["nodes"][1]
        self.assertEqual(node["state"], "error: no answer within 1 s")
        self.assertIn({"node": "node-a", "level": "WARN", "code": "peer_unreachable", "text": node["state"],
                       "data": {"state": node["state"]}}, rep["alerts"])

    def test_json_answer_from_too_old_peer(self):
        a = self.peer_state("node-a")
        (a / "history.log").write_text(block(600))
        (a / "launchctl.txt").write_text(launchctl_text())
        (a / "version").write_text("1.3.2")  # knows `monitor` (a dev build) but reports a version below 1.3.3
        self.node(block(600), launchctl_text())
        rep = self.gather([self.peer_spec("node-a")], {"node-a": a})
        node = rep["nodes"][1]
        self.assertEqual((node["state"], node["version"]), ("docsync too old on node-a (needs 1.3.3)", "1.3.2"))
        self.assertEqual([(x["code"], x["data"]) for x in rep["alerts"]], [("peer_too_old", {"version": "1.3.2"})])

    def test_conflict_scan_budget(self):
        import threading
        from docsync import monitor
        gate, calls = threading.Event(), []

        def slow(cfg):
            calls.append(1)
            gate.wait(10)
            return {"documents": ["a.conflict-x"] * len(calls)}
        t0 = time.monotonic()
        with mock.patch.object(monitor, "conflict_copies", slow), mock.patch.object(monitor, "CONFLICT_BUDGET", 0.3):
            node = self.node(block(600), launchctl_text())
            self.assertLess(time.monotonic() - t0, 2.5)
            self.assertIsNone(node["conflicts"])
            for _ in range(3):  # --interval refreshes while the first scan still runs: no second scan
                self.assertIsNone(monitor.bounded_conflicts(self.cfg))
            self.assertEqual(len(calls), 1)
            self.assertEqual(sum(t.name == "docsync-monitor-conflicts" and t.is_alive() for t in threading.enumerate()), 1)
            gate.set()
            monitor._SCAN["thread"].join(5)
            self.assertEqual(monitor.bounded_conflicts(self.cfg, budget=5), {"documents": 2})  # a new scan, finished
            self.assertEqual(len(calls), 2)
            gate.clear()
            self.assertEqual(monitor.bounded_conflicts(self.cfg, budget=0.1), {"documents": 2})  # late: the last result
            gate.set()

    def test_local_and_peers_run_in_parallel(self):
        from docsync import monitor, monitor_report

        def slow_local(cfg, state_dir=None, log_dir=None):
            time.sleep(1.5)
            return monitor_report.stub_node("node-c", "ok-ish")

        def slow_peer(p, ssh_cmd=None, timeout=None):
            time.sleep(1.5)
            return monitor_report.stub_node(p.name, "unreachable")
        specs = [self.peer_spec("node-a"), self.peer_spec("node-b")]
        with mock.patch.object(monitor, "local_node", slow_local), mock.patch.object(monitor_report, "probe_peer", slow_peer):
            t0 = time.monotonic()
            rep = monitor_report.gather(self.cfg, specs)
            took = time.monotonic() - t0
        self.assertLess(took, 2.6)  # the maximum of the two, not the sum (3 s)
        self.assertEqual([n["node"] for n in rep["nodes"]], ["node-c", "node-a", "node-b"])

        def hung_peer(p, ssh_cmd=None, timeout=None):
            time.sleep(4)
            return monitor_report.stub_node(p.name, "late")
        with mock.patch.object(monitor_report, "probe_peer", hung_peer), mock.patch.object(monitor_report, "TOTAL_BUDGET", 0.5):
            t0 = time.monotonic()
            rep = monitor_report.gather(self.cfg, specs[:1], state_dir=self.state, log_dir=self.logs)
            took = time.monotonic() - t0
        self.assertLess(took, 3)  # capped: the hung peer is reported, not waited for
        self.assertEqual(rep["nodes"][1]["state"], "error: no answer within the monitor budget of 0.5 s")

    def test_agent_old_only_below_floor(self):
        from docsync import monitor
        (Path(self.agent_py).parent / "version").write_text(monitor.AGENT_MIN_VERSION)
        with mock.patch("docsync.__version__", "1.9.0"):  # a newer dev checkout runs the monitor
            node = self.node(block(600), launchctl_text())
        self.assertEqual((node["agent"]["program_ok"], self.codes(node)), (True, []))
        (Path(self.agent_py).parent / "version").write_text("1.3.1")
        self.assertEqual(self.node(block(600), launchctl_text())["agent"]["foreign_reason"], "old 1.3.1")

    def test_connect_timeout_comes_first(self):
        from docsync import probe

        class T:
            ssh_cmd, userhost = ["ssh", "-o", "ConnectTimeout=15", "-o", "BatchMode=yes"], "me@host"
        seen = []

        class FakePopen:
            def __init__(self, argv, **kw):
                seen.append(argv)
                self.args, self.returncode, self.pid = argv, 0, 0

            def communicate(self, *a, **kw):
                return "", ""
        with mock.patch.object(probe.subprocess, "Popen", FakePopen):
            probe.run(T(), "monitor --local --json", timeout=1, connect_timeout=5)
        self.assertEqual(seen[0][:4], ["ssh", "-o", "ConnectTimeout=5", "-o"])  # ssh keeps the first value
        self.assertEqual(seen[0][-2:], ["me@host", "/bin/sh -s"])

    def test_exit2_judged_by_the_last_agent_run(self):
        fail = "── node-b @ ?\n   FAILED after 0.0s: none of 10.42.0.2 answered a ping\n"
        agent_run = block(900, peers="1 ok, 1 failed (node-b)", body=fail)       # the agent: exit 2
        for later in ("heal", "no"):                                              # then a clean non-agent sync
            hist = agent_run + block(300, agent=later)
            node = self.node(hist, launchctl_text(last_exit="2"))
            self.assertEqual(node["last_agent_run"]["failed"], 1)
            codes = self.codes(node)
            self.assertNotIn(("FAIL", "agent_exit"), codes, later)
            self.assertEqual(codes, [] if later == "heal" else [("WARN", "non_agent_run")])
            self.assertAlmostEqual(node["last_success"]["age_s"], 300, delta=5)  # heal runs count as successes
        # the agent's own last run was clean: exit 2 is unexplained
        node = self.node(block(900) + block(300, agent="heal"), launchctl_text(last_exit="2"))
        self.assertEqual(self.codes(node), [("FAIL", "agent_exit")])
        # no origin lines yet (before 1.3.3): fall back to the last run
        node = self.node(block(300, agent=None, peers="1 ok, 1 failed (node-b)", body=fail), launchctl_text(last_exit="2"))
        self.assertIsNone(node["last_agent_run"])
        self.assertEqual(self.codes(node), [("WARN", "peer_failed")])

    def test_heal_origin(self):
        from docsync import cli, monitor
        self.assertTrue(monitor.origin_line(argv=["docsync", "sync"], ppid=1, from_heal=True).endswith(" | agent=heal"))
        self.assertTrue(cli.build_parser().parse_args(["plan", "--from-heal"]).from_heal)
        h = monitor.parse_history(block(60, agent="heal"))
        self.assertEqual(h["last_run"]["origin"]["by"], "heal")
        self.assertIsNone(h["last_agent_run"])
        self.assertEqual((h["last_synced"]["age_s"] > 0, h["last_success"] is not None), (True, True))
