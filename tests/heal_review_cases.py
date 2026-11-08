"""`docsync heal` review fixes: remote-install flags, explicit coordinator, verify and give-up,
loaded agent_exit, one notification per episode, from-agent sync, corrupt state, FM compile budget,
dumps only from the log dir, stale_success when no peer could be synced."""
import json
import os
import subprocess
import time
from unittest import mock

try:
    from tests.heal_cases import T0, FakeRunner, HealBase, alert
except ImportError:
    from heal_cases import T0, FakeRunner, HealBase, alert

from docsync import agent, heal, heal_cli, heal_fm, heal_rules

DOWN_RUN = {"ok": 0, "failed": 1, "busy": 0,
            "failed_peers": [{"peer": "node-b", "root": "", "reason": "none of 10.42.0.2 answered a ping"}]}


class HealCoordinatorTests(HealBase):
    def test_update_peer_passes_no_config_and_the_ssh_target_user(self):
        self.src.mkdir()
        r = FakeRunner()
        self.healer(r).run([alert("node-b", "peer_too_old", version="1.2.0")])
        argv = r.argvs()[0]
        self.assertIn("--no-config", argv)
        self.assertEqual(argv[argv.index("--user") + 1], "mafoe")
        self.assertIn("--no-ssh-config", argv)  # else the coordinator's ~/.ssh/config.d gets User mafoe for 10.42.0.*

    def test_missing_maccluster_source_gives_up_instead_of_notifying_forever(self):
        r = FakeRunner()
        for _ in range(6):
            self.healer(r).run([alert("node-b", "peer_too_old", version="1.2.0")])
            self.t += 31 * 60
        notes = [n for n in r.notes() if "update_peer" in n or "peer_too_old" in n]
        self.assertEqual(len(notes), 4)  # 3 failures, then one give-up
        self.assertTrue(any("heal gave up on peer_too_old at node-b" in n for n in notes))
        self.assertEqual(r.argvs(), [])

    def test_update_peer_without_ssh_target_fails_without_remote_install(self):
        self.src.mkdir()
        nodes = {"node-a": {"id": "node-a"}, "node-b": {"id": "node-b", "ip": "10.42.0.2"}}
        r = FakeRunner()
        self.healer(r, ctx=self.ctx(nodes=nodes)).run([alert("node-b", "peer_too_old", version="1.2.0")])
        self.assertEqual(r.argvs(), [])
        self.assertIn("no user in ssh_target of node-b", self.history())

    def remote_alerts(self):
        return [alert("node-b", "agent_foreign"), alert("node-b", "peer_too_old", version="1.0"),
                alert("node-b", "peer_unreachable"), alert("node-b", "agent_not_loaded"),
                alert("node-a", "peer_failed", level="WARN", peers=["node-b"],
                      reasons=["node-b: none of 10.42.0.2 answered a ping"])]

    def test_a_peer_without_the_flag_never_acts_on_other_nodes(self):
        self.src.mkdir()
        r = FakeRunner()
        self.healer(r, ctx=self.ctx(coordinator=False)).run(self.remote_alerts())
        self.assertFalse(any("maccluster" in a or a == "/sbin/ping" for argv in r.argvs() for a in argv))
        self.assertEqual(self.wol, [])

    def test_the_coordinator_does(self):
        self.src.mkdir()
        r = FakeRunner()
        self.healer(r).run(self.remote_alerts())
        joined = [" ".join(a) for a in r.argvs()]
        self.assertTrue(any("sync setup --node node-b" in j for j in joined))
        self.assertTrue(any("remote-install" in j for j in joined))
        self.assertTrue(any(j.startswith("/sbin/ping") for j in joined))

    def test_default_context_is_coordinator_only_with_the_flag(self):
        cluster = self.base / "cluster.toml"
        cluster.write_text('[[nodes]]\nid = "node-a"\nip = "10.42.0.1"\n')
        with mock.patch.object(heal, "MACCLUSTER_CONFIG", cluster), \
                mock.patch("shutil.which", return_value="/bin/maccluster"):
            self.assertFalse(heal.default_context().coordinator)
            self.assertTrue(heal.default_context(coordinator=True).coordinator)

    def test_cli_and_plist_carry_the_flag(self):
        status = self.base / "status.json"
        status.write_text("[]")
        for argv, want in ((["--once", "--dry-run", "--no-fm"], False), (["--once", "--coordinator", "--no-fm"], True)):
            with self.subTest(argv=argv), mock.patch.dict(os.environ, {"DOCSYNC_TEST_HEAL_STATUS": str(status)}), \
                    mock.patch.object(heal, "default_context", return_value=self.ctx(coordinator=want)) as dc, \
                    mock.patch("builtins.print"):
                heal_cli.main(argv)
                dc.assert_called_once_with(coordinator=want)
        self.assertIn("<string>--once</string><string>--coordinator</string>", agent.heal_plist_xml(15, True))
        self.assertNotIn("--coordinator", agent.heal_plist_xml(15))


class HealEpisodeTests(HealBase):
    def test_ok_but_still_present_counts_as_failure_and_gives_up(self):
        r = FakeRunner()
        a = [alert("node-b", "agent_foreign")]
        for _ in range(5):
            self.healer(r).run(a)
            self.t += 31 * 60
        self.assertEqual(len(r.argvs()), 3)
        gave = [n for n in r.notes() if "heal gave up on agent_foreign at node-b: agent_foreign still present" in n]
        self.assertEqual(len(gave), 1)

    def test_setup_node_verifies_with_a_fresh_probe(self):
        r = FakeRunner()
        self.healer(r, reprobe=lambda node: ["agent_foreign"]).run([alert("node-b", "agent_foreign")])
        self.assertIn("setup_node (rule): failed: agent_foreign still reported by node-b", self.history())
        res = self.healer(FakeRunner(), reprobe=lambda node: []).run([alert("node-b", "agent_no_max_runtime")])
        self.assertEqual(res.exit_code, 0)
        self.assertIn("verified by a fresh monitor probe", self.history())

    def test_loaded_agent_exit_notifies_with_the_dump_and_never_bootstraps(self):
        self.plist.write_text("<plist/>")
        (self.logs / "deadline-20261108T020000Z.txt").write_text('Thread MainThread\n  File "transport.py", line 9\n')
        r = FakeRunner()
        self.healer(r).run([alert("node-a", "agent_exit", exit=4)])
        self.assertEqual(r.argvs(), [])
        self.assertTrue(any("last run exited 4" in n and "deadline-20261108T020000Z.txt" in n for n in r.notes()))

    def test_one_notification_per_episode_plus_a_daily_reminder(self):
        r = FakeRunner({"docsync advise": (0, "• ok")})
        a = [alert("node-a", "icloud_incomplete", level="WARN", roots=["documents"]), alert("node-a", "conflicts", count=1)]
        for step in (0, 31 * 60, 2 * 3600):
            self.t = T0 + step
            self.healer(r).run(a)
        self.assertEqual(len(r.notes()), 2)
        self.assertEqual(len(r.argvs()), 1)  # docsync advise once
        self.assertIn("skipped: already notified", self.history())
        self.t = T0 + 25 * 3600
        self.healer(r).run(a)
        self.assertEqual(len(r.notes()), 4)
        self.healer(r).run([])  # the episode ends
        self.t += 60
        self.healer(r).run(a)
        self.assertEqual(len(r.notes()), 6)

    def test_corrupt_state_entries_are_dropped(self):
        key = "node-b|agent_foreign|setup_node"
        for bad in ({key: {"last": "x", "fails": 0}}, {key: 5}, {key: {"last": 1, "fails": True}},
                    {key: {"last": time.time(), "fails": 0, "ok_at": "y"}}, [1, 2], "x"):
            with self.subTest(bad=bad):
                (self.state / "heal-state.json").write_text(json.dumps(bad))
                r = FakeRunner()
                self.healer(r).run([alert("node-b", "agent_foreign")])
                self.assertEqual(r.argvs(), [["/bin/maccluster", "sync", "setup", "--node", "node-b"]])


class HealBudgetTests(HealBase):
    def test_no_compile_with_less_than_120_s_left(self):
        env = {k: v for k, v in os.environ.items() if k != "DOCSYNC_TEST_FM"}
        with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(heal_fm, "needs_compile", return_value=True), \
                mock.patch.object(heal_fm, "helper_ready", side_effect=AssertionError("compiled")):
            self.assertIsNone(heal_fm.choose([], {}, {"conflicts": ["advise_conflicts"]}, budget=100))
        done = subprocess.CompletedProcess([], 0, '{"decisions": []}', "")
        with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(heal_fm, "needs_compile", return_value=True), \
                mock.patch.object(heal_fm, "helper_ready", return_value=(True, "")) as ready, \
                mock.patch.object(heal_fm.subprocess, "run", return_value=done):
            self.assertEqual(heal_fm.choose([], {}, {"conflicts": ["advise_conflicts"]}, budget=300), [])
        self.assertEqual(ready.call_args.kwargs["timeout"], 240)

    def test_healer_passes_its_budget_to_the_fm(self):
        with mock.patch.object(heal_fm, "choose", return_value=None) as choose:
            self.healer(FakeRunner(), use_fm=True, budget_end=50.0, clock=lambda: 10.0).run(
                [alert("node-a", "non_agent_run", level="WARN")])
        self.assertEqual(choose.call_args.kwargs["budget"], 40.0)

    def test_availability_survives_a_hanging_helper(self):
        with mock.patch.object(heal_fm, "helper_ready", return_value=(True, "")), \
                mock.patch.object(heal_fm.subprocess, "run", side_effect=subprocess.TimeoutExpired(["x"], 60)):
            ok, why = heal_fm.availability()
        self.assertFalse(ok)
        self.assertIn("helper check failed", why)

    def test_dump_paths_outside_the_log_dir_are_ignored(self):
        outside = self.base / "secret" / "deadline-1.txt"
        outside.parent.mkdir()
        outside.write_text("Thread MainThread\n")
        r = FakeRunner()
        self.healer(r).run([alert("node-a", "deadline_dump", level="WARN", paths=[str(outside)])])
        self.assertIn("summarize_dump (rule): skipped: no deadline dump", self.history())
        self.assertEqual(r.notes(), [])


class HealStaleTests(HealBase):
    def stale(self, run):
        return {**alert("node-a", "stale_success", age_min=180), "last_run": run}

    def test_stale_with_no_reachable_peer_wakes_instead_of_syncing(self):
        r = FakeRunner()
        self.healer(r).run([self.stale(DOWN_RUN)])
        self.assertEqual(self.wol, ["aa:bb:cc:dd:ee:ff"])
        self.assertFalse(any("sync" in argv for argv in r.argvs()))
        self.assertIn("stale_success → wake_peer (rule): ok: node-b", self.history())

    def test_stale_without_coordinator_or_with_busy_peers_notifies(self):
        busy = {"ok": 0, "failed": 0, "busy": 2, "failed_peers": []}
        self.assertEqual(heal_rules.rule_action(self.stale(DOWN_RUN), self.ctx(coordinator=False)), "notify")
        self.assertEqual(heal_rules.rule_action(self.stale(busy), self.ctx()), "notify")
        self.assertEqual(heal_rules.rule_action(self.stale({"ok": 1, "failed": 1, "busy": 0}), self.ctx()), "sync_now")
        self.assertEqual(heal_rules.rule_action(self.stale(None), self.ctx()), "sync_now")

    def test_stale_with_a_peer_alert_in_the_same_pass_never_syncs(self):
        ok_run = {"ok": 1, "failed": 1, "busy": 0, "failed_peers": []}
        failed = alert("node-a", "peer_failed", level="WARN", peers=["node-b"],
                       reasons=["node-b: none of 10.42.0.2 answered a ping"])
        down = alert("node-b", "peer_unreachable", level="WARN")
        for other in (failed, down):
            with self.subTest(other=other["code"]):
                both = [self.stale(ok_run), other]
                self.assertEqual(heal_rules.allowed_actions(both[0], self.ctx(), both), ["wake_peer", "notify"])
                self.assertEqual(heal_rules.wake_targets(both[0], both), ["node-b"])
                self.assertEqual(heal_rules.allowed_actions(both[0], self.ctx(coordinator=False), both), ["notify"])
        broken = alert("node-a", "peer_failed", level="WARN", peers=["node-b"], reasons=["node-b: rsync error 23"])
        self.assertEqual(heal_rules.allowed_actions(self.stale(ok_run), self.ctx(), [broken]), ["notify"])
        r = FakeRunner()
        self.healer(r).run([self.stale(ok_run), down])
        self.assertFalse(any("sync" in argv for argv in r.argvs()))
        self.assertIn("stale_success → wake_peer (rule): ok: node-b", self.history())

    def test_stale_when_every_peer_failed(self):
        run = {"ok": 0, "failed": 2, "busy": 0, "failed_peers": [
            {"peer": "node-b", "reason": "none of 10.42.0.2 answered a ping"}, {"peer": "node-d", "reason": "rsync 23"}]}
        self.assertEqual(heal_rules.rule_action(self.stale(run), self.ctx()), "wake_peer")

    def test_sync_now_counts_as_ok_only_when_stale_is_gone_next_pass(self):
        def start(argv):
            (self.state / "run.lock").write_bytes(b"777\n1.0\ndocsync sync\n")
            return (0, "777")
        r = FakeRunner({"docsync sync": start})
        for _ in range(5):
            self.healer(r).run([self.stale(None)])
            self.t += 31 * 60
        self.assertEqual(len([a for a in r.argvs() if "sync" in a]), 3)
        self.assertTrue(any("heal gave up on stale_success at node-a: stale_success still present after sync_now" in n
                            for n in r.notes()))
        self.assertIn("--from-heal", heal.SYNC_ARGS)
        self.assertNotIn("--from-agent", heal.SYNC_ARGS)

    def test_cli_attaches_the_nodes_last_run(self):
        status = self.base / "status.json"
        status.write_text(json.dumps([{"node": "node-a", "last_run": DOWN_RUN,
                                       "alerts": [alert("node-a", "stale_success", age_min=180)]}]))
        with mock.patch.dict(os.environ, {"DOCSYNC_TEST_HEAL_STATUS": str(status)}):
            got = heal_cli.collect_alerts(self.ctx())
        self.assertEqual(got[0]["last_run"], DOWN_RUN)


HEAL_REVIEW_CASES = (HealCoordinatorTests, HealEpisodeTests, HealBudgetTests, HealStaleTests)
