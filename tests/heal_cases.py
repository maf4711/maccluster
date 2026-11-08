"""`docsync heal`: rule table, actions, FM gate, rate limit, give-up, dry-run and the hard bans.
Every external effect goes through a recording fake runner; a guard fails any real subprocess,
socket or signal. IPs like 10.42.0.x only ever reach the fake runner."""
import io
import json
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from docsync import heal, heal_rules, lock

T0 = 1_800_000_000.0
NODES = {"node-a": {"id": "node-a", "ip": "10.42.0.1"},
         "node-b": {"id": "node-b", "ip": "10.42.0.2", "wake_mac": "aa:bb:cc:dd:ee:ff",
                    "ssh_target": "mafoe@10.42.0.2"}}
AGENT_PRINT = """gui/501/com.a321.docsync = {
\tstate = not running
\targuments = {
\t\tPY
\t\t-m
\t\tdocsync
\t\tsync
\t\t--all
\t\t--max-runtime
\t\t1800
\t}
\tlast exit code = 0
}
"""


def alert(node, code, level="FAIL", **data):
    return {"node": node, "level": level, "code": code, "text": f"{code} on {node}", "data": data}


class FakeRunner:
    """Records every argv; replies by the first key that is a substring of the joined argv."""

    def __init__(self, replies=None):
        self.calls, self.replies = [], dict(replies or {})

    def __call__(self, argv, timeout=60, env=None, detach=False, cwd=None):
        self.calls.append({"argv": list(argv), "env": env, "detach": detach, "timeout": timeout, "cwd": cwd})
        joined = " ".join(argv)
        for key, rep in self.replies.items():
            if key in joined:
                return rep(argv) if callable(rep) else rep
        return (0, "docsync 1.3.3") if argv[1:] == ["-m", "docsync", "--version"] else (0, "")

    def argvs(self, skip_notify=True):
        """Actions only: no notifications and no `-m docsync --version` probes of the agent check."""
        return [c["argv"] for c in self.calls if not (skip_notify and c["argv"][0].endswith("osascript"))
                and c["argv"][1:] != ["-m", "docsync", "--version"]]

    def notes(self):
        return [c["argv"][2] for c in self.calls if c["argv"][0].endswith("osascript")]


class HealBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.state, self.logs = self.base / "state", self.base / "logs"
        self.state.mkdir()
        self.logs.mkdir()
        self.plist = self.base / "com.a321.docsync.plist"
        self.src = self.base / "maccluster-src"
        self.py = str(self.base / "bin" / "python3")
        Path(self.py).parent.mkdir()
        Path(self.py).write_text("#!/bin/sh\n")
        os.chmod(self.py, 0o755)
        self.t = T0
        self.wol = []
        self.kills = []
        guards = [mock.patch("subprocess.Popen", side_effect=AssertionError("real subprocess in a heal test")),
                  mock.patch("os.system", side_effect=AssertionError("os.system in a heal test")),
                  mock.patch("socket.socket", side_effect=AssertionError("real socket in a heal test")),
                  mock.patch("os.kill", side_effect=self._kill),
                  mock.patch.dict(os.environ, {"DOCSYNC_TEST_FM": "unavailable"})]
        for g in guards:
            g.start()
            self.addCleanup(g.stop)
        self.addCleanup(self.tmp.cleanup)

    def _kill(self, pid, sig):
        self.kills.append((pid, sig))
        if sig != 0:
            raise AssertionError(f"heal sent signal {sig} to {pid}")
        raise ProcessLookupError

    def ctx(self, coordinator=True, node="node-a", nodes=None):
        return heal.Context(node=node, coordinator=coordinator, cluster_nodes=dict(NODES if nodes is None else nodes),
                            python=self.py, state_dir=self.state, log_dir=self.logs, maccluster="/bin/maccluster",
                            maccluster_src=self.src, plist=self.plist, uid=501)

    def healer(self, runner, ctx=None, **kw):
        kw.setdefault("use_fm", False)
        return heal.Healer(ctx or self.ctx(), runner, now=lambda: self.t, sleep=lambda s: None,
                           alive=kw.pop("alive", lambda pid: False), send_wol=self.wol.append, **kw)

    def agent_print(self, py=None):
        return AGENT_PRINT.replace("PY", py or self.py)

    def history(self):
        f = self.state / "history.log"
        return f.read_text() if f.exists() else ""


class HealRuleTests(HealBase):
    def test_each_code_maps_to_its_action_on_the_coordinator(self):
        cases = [("node-a", "agent_not_loaded", "reload_agent"), ("node-a", "agent_exit", "notify"),
                 ("node-b", "agent_not_loaded", "setup_node"), ("node-b", "agent_exit", "setup_node"),
                 ("node-b", "agent_foreign", "setup_node"), ("node-a", "agent_no_max_runtime", "setup_node"),
                 ("node-a", "stale_success", "sync_now"), ("node-b", "peer_unreachable", "wake_peer"),
                 ("node-b", "peer_too_old", "update_peer"), ("node-a", "deadline_dump", "summarize_dump"),
                 ("node-a", "conflicts", "advise_conflicts"), ("node-a", "held_deletes", "advise_deletes"),
                 ("node-a", "non_agent_run", "notify"), ("node-b", "icloud_incomplete", "notify"),
                 ("node-a", "peer_lock_lost", "none"), ("node-a", "never_synced", "none"),
                 ("node-a", "peer_failed", "notify")]
        for node, code, want in cases:
            with self.subTest(code=code, node=node):
                self.assertEqual(heal_rules.rule_action(alert(node, code), self.ctx()), want)

    def test_non_coordinator_heals_only_itself(self):
        ctx = self.ctx(coordinator=False)
        self.assertEqual(heal_rules.rule_action(alert("node-a", "agent_not_loaded"), ctx), "reload_agent")
        self.assertEqual(heal_rules.rule_action(alert("node-a", "stale_success"), ctx), "sync_now")
        for code in ("agent_not_loaded", "agent_foreign", "peer_unreachable", "peer_too_old", "icloud_incomplete"):
            with self.subTest(code=code):
                self.assertEqual(heal_rules.allowed_actions(alert("node-b", code), ctx), [])
        self.assertEqual(heal_rules.allowed_actions(alert("node-a", "agent_foreign"), ctx), [])

    def test_peer_failed_wakes_unreachable_peers_else_notifies(self):
        down = alert("node-a", "peer_failed", level="WARN", peers=["node-b"],
                     reasons=["node-b [documents]: none of 10.42.0.2, b.local answered a ping"])
        broken = alert("node-a", "peer_failed", level="WARN", peers=["node-b"], reasons=["node-b: rsync error 23"])
        self.assertEqual(heal_rules.unreachable_peers(down), ["node-b"])
        self.assertEqual(heal_rules.allowed_actions(down, self.ctx()), ["wake_peer", "notify"])
        self.assertEqual(heal_rules.rule_action(broken, self.ctx()), "notify")
        self.assertEqual(heal_rules.rule_action(down, self.ctx(coordinator=False)), "notify")
        r = FakeRunner()
        self.healer(r).run([down])
        self.assertEqual(self.wol, ["aa:bb:cc:dd:ee:ff"])
        self.assertEqual(r.argvs(), [["/sbin/ping", "-c", "1", "-t", "2", "10.42.0.2"]])
        self.assertIn("peer_failed → wake_peer (rule): ok: node-b 10.42.0.2 answers ping", self.history())

    def test_setup_node_needs_the_node_in_cluster_toml(self):
        self.assertEqual(heal_rules.rule_action(alert("node-x", "agent_foreign"), self.ctx()), "none")
        self.assertEqual(heal_rules.rule_action(alert("node-x", "peer_too_old"), self.ctx()), "none")


class HealActionTests(HealBase):
    def test_reload_agent_bootstraps_the_plist_and_verifies(self):
        self.plist.write_text("<plist/>")
        r = FakeRunner({"launchctl print": (0, self.agent_print())})
        res = self.healer(r).run([alert("node-a", "agent_not_loaded")])
        self.assertEqual(r.argvs()[0], ["launchctl", "bootstrap", "gui/501", str(self.plist)])
        self.assertEqual(r.argvs()[1], ["launchctl", "print", "gui/501/com.a321.docsync"])
        self.assertEqual(res.exit_code, 0)
        self.assertIn("alert node-a agent_not_loaded → reload_agent (rule): ok", self.history())

    def test_reload_agent_installs_when_the_plist_is_missing(self):
        r = FakeRunner({"launchctl print": (0, self.agent_print())})
        self.healer(r).run([alert("node-a", "agent_not_loaded")])
        self.assertEqual(r.argvs()[0], [self.py, "-m", "docsync", "install-agent", "--interval", "15"])

    def test_reload_agent_fails_when_the_agent_lacks_max_runtime(self):
        self.plist.write_text("<plist/>")
        r = FakeRunner({"launchctl print": (0, self.agent_print().replace("\t\t--max-runtime\n\t\t1800\n", ""))})
        res = self.healer(r).run([alert("node-a", "agent_not_loaded")])
        self.assertEqual(res.exit_code, 1)
        self.assertIn("reload_agent (rule): failed", self.history())

    def test_reload_agent_accepts_a_healthy_agent_under_another_python(self):
        other = self.base / "other-python"
        other.write_text("#!/bin/sh\n")
        os.chmod(other, 0o755)
        self.plist.write_text("<plist/>")
        r = FakeRunner({"launchctl print": (0, self.agent_print(str(other)))})
        res = self.healer(r).run([alert("node-a", "agent_not_loaded")])
        self.assertEqual(res.exit_code, 0)
        self.assertIn("agent loaded (docsync 1.3.3)", self.history())
        probe = [c for c in r.calls if c["argv"] == [str(other), "-m", "docsync", "--version"]][0]
        self.assertEqual(probe["cwd"], "/")

    def test_reload_agent_fails_a_foreign_agent(self):
        self.plist.write_text("<plist/>")
        shim = self.agent_print().replace(f"\t\t{self.py}\n\t\t-m\n\t\tdocsync\n", f"\t\t{self.py}\n\t\t/x/docsync-launchd.py\n")
        cases = {"shim": shim, "missing interpreter": self.agent_print("/nonexistent/python3"),
                 "old 1.3.1": self.agent_print()}
        for reason, text in cases.items():
            with self.subTest(reason=reason):
                r = FakeRunner({"launchctl print": (0, text), "--version": (0, "docsync 1.3.1")})
                self.t += 31 * 60
                self.healer(r).run([alert("node-a", "agent_not_loaded")])
                self.assertIn(f"agent is foreign ({reason})", self.history())

    def test_sync_now_skips_while_a_run_holds_the_lock(self):
        (self.state / "run.lock").write_bytes(b"4242\n1.0\ndocsync sync\n")
        r = FakeRunner()
        self.healer(r, alive=lambda pid: pid == 4242).run([alert("node-a", "stale_success", age_min=300)])
        self.assertEqual(r.argvs(), [])
        self.assertIn("skipped: run in progress (pid 4242)", self.history())

    def test_sync_now_starts_one_detached_sync_and_sees_it_take_the_lock(self):
        def start(argv):
            (self.state / "run.lock").write_bytes(b"777\n1.0\ndocsync sync\n")
            return (0, "777")
        r = FakeRunner({"docsync sync": start})
        res = self.healer(r).run([alert("node-a", "stale_success", age_min=300)])
        call = [c for c in r.calls if "sync" in c["argv"]][0]
        self.assertEqual(call["argv"], [self.py, "-m", "docsync", "sync", "--all", "--max-runtime", "1800", "--no-notify", "--from-heal"])
        self.assertTrue(call["detach"])
        self.assertEqual(res.exit_code, 0)
        self.assertEqual(lock.holder(self.state)["pid"], 777)

    def test_wake_peer_sends_magic_packet_then_pings_the_tb_ip(self):
        tries = iter([(2, ""), (0, "1 packets received")])
        r = FakeRunner({"/sbin/ping": lambda argv: next(tries)})
        res = self.healer(r).run([alert("node-b", "peer_unreachable")])
        self.assertEqual(self.wol, ["aa:bb:cc:dd:ee:ff"])
        self.assertEqual(r.argvs()[0], ["/sbin/ping", "-c", "1", "-t", "2", "10.42.0.2"])
        self.assertEqual(len(r.argvs()), 2)
        self.assertEqual(res.exit_code, 0)

    def test_magic_packet_layout(self):
        pkt = heal.magic_packet("aa:bb:cc:dd:ee:ff")
        self.assertEqual(pkt[:6], b"\xff" * 6)
        self.assertEqual(pkt[6:], bytes.fromhex("aabbccddeeff") * 16)

    def test_setup_node_runs_maccluster_sync_setup(self):
        r = FakeRunner()
        self.healer(r).run([alert("node-b", "agent_foreign", program=["/usr/bin/python3", "x.py"])])
        self.assertEqual(r.argvs(), [["/bin/maccluster", "sync", "setup", "--node", "node-b"]])

    def test_update_peer_fails_with_a_notification_without_maccluster_source(self):
        r = FakeRunner()
        self.healer(r).run([alert("node-b", "peer_too_old", version="1.2.0")])
        self.assertEqual(r.argvs(), [])
        self.assertTrue(any("maccluster" in n and "update_peer" in n for n in r.notes()))

    def test_update_peer_remote_installs_then_sets_up(self):
        self.src.mkdir()
        r = FakeRunner()
        self.healer(r).run([alert("node-b", "peer_too_old", version="1.2.0")])
        self.assertEqual(r.argvs(), [["/bin/maccluster", "remote-install", "10.42.0.2", "--no-config", "--no-ssh-config",
                                      "--user", "mafoe"],
                                     ["/bin/maccluster", "sync", "setup", "--node", "node-b"]])
        self.assertEqual(r.calls[0]["env"]["MACCLUSTER_SRC"], str(self.src))

    def test_summarize_dump_notifies_with_the_fm_summary(self):
        dump = self.logs / "deadline-20261108T010000Z.txt"
        dump.write_text("Thread MainThread\n  File x.py, line 3, in wait\n")
        canned = self.base / "fm.json"
        canned.write_text(json.dumps({"summary": "Der Lauf hing in wait (x.py)."}))
        r = FakeRunner()
        with mock.patch.dict(os.environ, {"DOCSYNC_TEST_FM": str(canned)}):
            self.healer(r, use_fm=True).run([alert("node-a", "deadline_dump", level="WARN",
                                                   paths=[str(dump)])])
        self.assertTrue(any("Der Lauf hing in wait" in n for n in r.notes()))

    def test_advise_conflicts_runs_docsync_advise_and_notifies_its_advice(self):
        r = FakeRunner({"docsync advise": (0, "── advisor\n• a.txt behalten")})
        self.healer(r).run([alert("node-a", "conflicts", count=1, roots={"documents": 1})])
        self.assertEqual(r.argvs(), [[self.py, "-m", "docsync", "advise"]])
        self.assertTrue(any("a.txt behalten" in n for n in r.notes()))
        self.assertIn("a.txt behalten", self.history())

    def test_advise_deletes_points_to_force_deletes_without_running_it(self):
        (self.state / "history.log").write_text(
            "\n=== 2026-11-08 10:00:00\n── node-b @ x\n   ⚠ deletion guard: 40 deletions exceed max_delete=25 — skipped\n"
            "══ TOTAL\n")
        r = FakeRunner()
        self.healer(r).run([alert("node-a", "held_deletes", count=40)])
        self.assertEqual(r.argvs(), [])
        self.assertTrue(any("review, then docsync sync --force-deletes" in n for n in r.notes()))

    def test_icloud_notification_names_the_switch_and_the_node(self):
        r = FakeRunner()
        self.healer(r).run([alert("node-b", "icloud_incomplete", level="WARN", roots=["documents"])])
        self.assertTrue(any("node-b" in n and "Desktop & Documents" in n for n in r.notes()))

    def test_no_notify_sends_nothing(self):
        r = FakeRunner()
        self.healer(r, notify=False).run([alert("node-a", "non_agent_run", level="WARN", origin="zsh")])
        self.assertEqual(r.notes(), [])
        self.assertIn("non_agent_run → notify (rule)", self.history())


class HealFlowTests(HealBase):
    def test_fm_disallowed_action_is_ignored_and_the_rule_decides(self):
        canned = self.base / "fm.json"
        canned.write_text(json.dumps({"decisions": [{"code": "conflicts", "action": "sync_now", "reason": "x"}]}))
        r = FakeRunner({"docsync advise": (0, "• ok")})
        with mock.patch.dict(os.environ, {"DOCSYNC_TEST_FM": str(canned)}):
            self.healer(r, use_fm=True).run([alert("node-a", "conflicts", count=1)])
        self.assertEqual(r.argvs(), [[self.py, "-m", "docsync", "advise"]])
        self.assertIn("fm proposed disallowed action sync_now", self.history())
        self.assertIn("conflicts → advise_conflicts (rule)", self.history())

    def test_fm_choice_is_used_with_its_reason(self):
        canned = self.base / "fm.json"
        canned.write_text(json.dumps({"decisions": [{"code": "stale_success", "action": "none",
                                                     "reason": "Der Peer schläft."}]}))
        r = FakeRunner()
        with mock.patch.dict(os.environ, {"DOCSYNC_TEST_FM": str(canned)}):
            self.healer(r, use_fm=True).run([alert("node-a", "stale_success", age_min=90)])
        self.assertEqual(r.argvs(), [])
        self.assertIn("stale_success → none (fm)", self.history())
        self.assertIn("Der Peer schläft.", self.history())

    def test_fm_unavailable_falls_back_to_rules(self):
        r = FakeRunner()
        self.healer(r, use_fm=True).run([alert("node-b", "agent_foreign")])
        self.assertIn("agent_foreign → setup_node (rule)", self.history())

    def test_rate_limit_once_per_30_min_per_node_code_action(self):
        r = FakeRunner()
        a = [alert("node-b", "agent_foreign")]
        self.healer(r).run(a)
        self.t += 10 * 60
        self.healer(r).run(a)
        self.assertEqual(len(r.argvs()), 1)
        self.assertIn("skipped: rate limit", self.history())
        self.t += 21 * 60
        self.healer(r).run(a)
        self.assertEqual(len(r.argvs()), 2)

    def test_three_failures_give_up_with_one_notification(self):
        r = FakeRunner({"maccluster": (1, "ssh: connect refused")})
        a = [alert("node-b", "agent_foreign")]
        for _ in range(4):
            self.healer(r).run(a)
            self.t += 31 * 60
        self.assertEqual(len(r.argvs()), 3)
        gave_up = [n for n in r.notes() if "heal gave up on agent_foreign at node-b: ssh: connect refused" in n]
        self.assertEqual(len(gave_up), 1)
        self.assertIn("skipped: gave up", self.history())

    def test_dry_run_does_nothing(self):
        self.plist.write_text("<plist/>")
        r = FakeRunner()
        res = self.healer(r, dry_run=True).run([alert("node-a", "agent_not_loaded"), alert("node-b", "peer_unreachable"),
                                                alert("node-a", "conflicts", count=2)])
        self.assertEqual(r.calls, [])
        self.assertEqual(self.wol, [])
        self.assertFalse((self.state / "heal-state.json").exists())
        self.assertEqual(self.history(), "")
        self.assertTrue(any("would run" in ln for ln in res.lines))
        self.assertEqual(res.exit_code, 1)

    def test_history_block_and_exit_codes(self):
        r = FakeRunner({"docsync advise": (0, "• ok")})
        res = self.healer(r).run([alert("node-b", "agent_foreign"), alert("node-a", "conflicts", count=1)])
        self.assertRegex(self.history(), r"\n=== heal \d{4}-\d\d-\d\d \d\d:\d\d:\d\d\n")
        self.assertEqual(res.exit_code, 1)  # conflicts stay with the user
        res = self.healer(FakeRunner()).run([alert("node-a", "non_agent_run", level="WARN")])
        self.assertEqual(res.exit_code, 0)


class HealBanTests(HealBase):
    def all_alerts(self):
        return [alert("node-a", "agent_not_loaded"), alert("node-b", "agent_exit", exit=1),
                alert("node-b", "agent_foreign"), alert("node-a", "agent_no_max_runtime"),
                alert("node-a", "stale_success"), alert("node-b", "peer_unreachable"),
                alert("node-b", "peer_too_old", version="1.0"), alert("node-a", "deadline_dump", paths=["deadline-1.txt"]),
                alert("node-a", "conflicts", count=1), alert("node-a", "held_deletes", count=40),
                alert("node-a", "non_agent_run"), alert("node-a", "icloud_incomplete", roots=["documents"]),
                alert("node-a", "peer_lock_lost")]

    def run_everything(self, **kw):
        self.src.mkdir(exist_ok=True)
        self.plist.write_text("<plist/>")
        (self.logs / "deadline-1.txt").write_text("Thread MainThread\n")
        (self.state / "history.log").write_text("\n=== 2026-11-08 10:00:00\n   ⚠ deletion guard: 40 deletions\n══ TOTAL\n")
        r = FakeRunner({"launchctl print": (0, self.agent_print()), "docsync advise": (0, "• Konfliktkopie a.conflict-x löschen")})
        self.healer(r, **kw).run(self.all_alerts())
        return r

    def test_never_passes_force_deletes(self):
        r = self.run_everything()
        self.assertFalse(any("--force-deletes" in a for argv in r.argvs() for a in argv))

    def test_never_deletes_or_renames_conflict_copies(self):
        docs = self.base / "Documents"
        docs.mkdir()
        (docs / "a.txt").write_text("1")
        (docs / "a.conflict-node-b-20261108.txt").write_text("2")
        before = sorted(p.name for p in docs.iterdir())
        def guard(real):
            def call(*args, **kw):
                if any(str(docs) in str(x) for x in args):
                    raise AssertionError(f"heal touched {args}")
                return real(*args, **kw)
            return call
        names = [(os, "remove"), (os, "unlink"), (os, "rename"), (os, "replace"), (shutil, "move"), (shutil, "rmtree")]
        patches = [mock.patch.object(mod, name, side_effect=guard(getattr(mod, name))) for mod, name in names]
        for pt in patches:
            pt.start()
        try:
            self.run_everything()
        finally:
            for pt in patches:
                pt.stop()
        self.assertEqual(sorted(p.name for p in docs.iterdir()), before)

    def test_never_edits_config_or_cluster_toml(self):
        cfg_file, cluster = self.base / "config.toml", self.base / "cluster.toml"
        cfg_file.write_text('path = "~/Documents"\n')
        cluster.write_text('[[nodes]]\nid = "node-a"\n')
        stamp = [(p.read_bytes(), p.stat().st_mtime_ns) for p in (cfg_file, cluster)]
        with mock.patch.object(heal, "CONFIG_FILE", cfg_file), mock.patch.object(heal, "MACCLUSTER_CONFIG", cluster):
            self.run_everything()
        self.assertEqual([(p.read_bytes(), p.stat().st_mtime_ns) for p in (cfg_file, cluster)], stamp)

    def test_never_kills_a_process(self):
        (self.state / "run.lock").write_bytes(b"4242\n1.0\ndocsync sync\n")
        r = self.run_everything(alive=heal._alive)
        self.healer(r, alive=heal._alive).run([alert("node-a", "stale_success")])  # alone: sync_now checks run.lock
        self.assertIn((4242, 0), self.kills)
        self.assertTrue(all(sig == 0 for _, sig in self.kills))
        words = ("kill", "pkill", "killall", "kickstart", "bootout")
        self.assertFalse(any(w in a for argv in r.argvs() for a in argv for w in words))


class HealCliTests(HealBase):
    def test_missing_monitor_exits_2(self):
        from docsync import heal_cli
        out = io.StringIO()
        with mock.patch.dict("sys.modules", {"docsync.monitor_alerts": None}), redirect_stdout(out):
            rc = heal_cli.main(["--once", "--dry-run"])
        self.assertEqual(rc, 2)
        self.assertIn("docsync monitor not available (needs docsync >= 1.3.3)", out.getvalue())

    def test_alerts_come_from_monitor_report_and_monitor_alerts(self):
        from docsync import heal_cli
        node = {"node": "node-b", "agent": {}}
        item = mock.Mock()
        item.to_dict.side_effect = lambda node=None: {"node": node, "level": "FAIL", "code": "agent_foreign",
                                                     "text": "shim", "data": {"program": [], "reason": "shim"}}
        fake_alerts = mock.Mock(alerts=mock.Mock(return_value=[item]))
        fake_report = mock.Mock(gather=mock.Mock(return_value={"nodes": [node]}))
        with mock.patch.dict("sys.modules", {"docsync.monitor_alerts": fake_alerts, "docsync.monitor_report": fake_report}), \
                mock.patch.object(heal_cli, "load_config", side_effect=FileNotFoundError):
            got = heal_cli.collect_alerts(self.ctx(coordinator=False))
        fake_report.gather.assert_called_once_with(None, local=True)
        fake_alerts.alerts.assert_called_once_with(node)
        self.assertEqual([(a["node"], a["code"]) for a in got], [("node-b", "agent_foreign")])

    def test_dry_run_with_injected_status(self):
        from docsync import heal_cli
        status = self.base / "status.json"
        status.write_text(json.dumps([{"node": "zz-test", "alerts": [alert("zz-test", "stale_success", age_min=500)]}]))
        out = io.StringIO()
        with mock.patch.dict(os.environ, {"DOCSYNC_TEST_HEAL_STATUS": str(status)}), \
                mock.patch.object(heal, "default_context", return_value=self.ctx(node="zz-test")), redirect_stdout(out):
            rc = heal_cli.main(["--once", "--dry-run", "--no-fm"])
        self.assertEqual(rc, 1)
        self.assertIn("alert zz-test stale_success → sync_now (rule): would run (dry-run)", out.getvalue())

    def test_heal_agent_plist(self):
        from docsync import agent
        xml = agent.heal_plist_xml(15)
        self.assertIn("<string>com.a321.docsync-heal</string>", xml)
        self.assertIn("<string>-m</string><string>docsync</string><string>heal</string><string>--once</string>"
                      "<string>--max-runtime</string><string>600</string>", xml)
        self.assertIn("<integer>900</integer>", xml)


HEAL_CASES = (HealRuleTests, HealActionTests, HealFlowTests, HealBanTests, HealCliTests)
