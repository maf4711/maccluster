"""docsync.heal_fm: structured choose() with Python-side validation, summarize(), and every way the
model can be missing (unavailable, timeout, bad output). No real helper runs here."""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from docsync import heal_fm

ALERTS = [{"node": "node-a", "level": "FAIL", "code": "conflicts", "text": "2 conflict copies", "data": {}},
          {"node": "node-b", "level": "FAIL", "code": "agent_foreign", "text": "agent runs x.py", "data": {}}]
ALLOWED = {"conflicts": ["advise_conflicts"], "agent_foreign": ["setup_node"]}


class HealFMTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        g = mock.patch("subprocess.Popen", side_effect=AssertionError("real subprocess in an FM test"))
        g.start()
        self.addCleanup(g.stop)

    def canned(self, obj) -> dict:
        f = Path(self.tmp.name) / "fm.json"
        f.write_text(obj if isinstance(obj, str) else json.dumps(obj))
        return {"DOCSYNC_TEST_FM": str(f)}

    def test_choose_parses_the_structured_reply(self):
        reply = {"decisions": [{"code": "conflicts", "action": "advise_conflicts", "reason": "Kopien sichten."},
                               {"code": "agent_foreign", "action": "none", "reason": "Wartung läuft."}]}
        with mock.patch.dict(os.environ, self.canned(reply)):
            got = heal_fm.choose(ALERTS, {"node": "node-a", "coordinator": True}, ALLOWED)
        self.assertEqual(got, reply["decisions"])

    def test_choose_drops_disallowed_actions_and_unknown_codes(self):
        reply = {"decisions": [{"code": "conflicts", "action": "rm -rf ~/Documents", "reason": "x"},
                               {"code": "agent_foreign", "action": "setup_node", "reason": "y"},
                               {"code": "made_up", "action": "notify", "reason": "z"}]}
        logs = []
        with mock.patch.dict(os.environ, self.canned(reply)):
            got = heal_fm.choose(ALERTS, {}, ALLOWED, log=logs.append)
        self.assertEqual(got, [{"code": "agent_foreign", "action": "setup_node", "reason": "y"}])
        self.assertIn("fm proposed disallowed action rm -rf ~/Documents", logs)
        self.assertIn("fm proposed unknown code made_up", logs)

    def test_choose_unavailable_returns_none(self):
        with mock.patch.dict(os.environ, {"DOCSYNC_TEST_FM": "unavailable"}):
            self.assertIsNone(heal_fm.choose(ALERTS, {}, ALLOWED))
            self.assertIsNone(heal_fm.summarize("stack", "deadline dump"))

    def test_choose_bad_output_returns_none(self):
        for bad in ("not json", json.dumps({"decisions": "x"}), json.dumps([1, 2]), json.dumps({"other": []})):
            with self.subTest(bad=bad), mock.patch.dict(os.environ, self.canned(bad)):
                self.assertIsNone(heal_fm.choose(ALERTS, {}, ALLOWED))

    def test_timeout_returns_none(self):
        env = {k: v for k, v in os.environ.items() if k != "DOCSYNC_TEST_FM"}
        with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(heal_fm, "helper_ready", return_value=(True, "")), \
                mock.patch.object(heal_fm.subprocess, "run",
                                  side_effect=subprocess.TimeoutExpired(["docsync-heal"], heal_fm.TIMEOUT)) as run:
            self.assertIsNone(heal_fm.choose(ALERTS, {}, ALLOWED))
            self.assertIsNone(heal_fm.summarize("stack", "deadline dump"))
        self.assertEqual(run.call_args.kwargs["timeout"], 20)

    def test_helper_exit_1_returns_none(self):
        env = {k: v for k, v in os.environ.items() if k != "DOCSYNC_TEST_FM"}
        done = subprocess.CompletedProcess(["docsync-heal"], 1, "", "model unavailable: Apple Intelligence is off")
        with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(heal_fm, "helper_ready", return_value=(True, "")), \
                mock.patch.object(heal_fm.subprocess, "run", return_value=done):
            self.assertIsNone(heal_fm.choose(ALERTS, {}, ALLOWED))

    def test_helper_not_compilable_returns_none(self):
        env = {k: v for k, v in os.environ.items() if k != "DOCSYNC_TEST_FM"}
        with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(heal_fm, "helper_ready", return_value=(False, "swiftc not found")):
            self.assertIsNone(heal_fm.choose(ALERTS, {}, ALLOWED))

    def test_prompt_lists_alerts_and_allowed_actions_only(self):
        prompt = heal_fm.choose_prompt(ALERTS, {"node": "node-a"}, ALLOWED)
        self.assertIn('"agent_foreign": ["setup_node", "none"]', prompt)
        self.assertIn("2 conflict copies", prompt)

    def test_summarize_reply_is_capped_at_three_sentences(self):
        reply = {"summary": "Eins. Zwei! Drei? Vier. Fünf."}
        with mock.patch.dict(os.environ, self.canned(reply)):
            self.assertEqual(heal_fm.summarize("stack", "deadline dump"), "Eins. Zwei! Drei?")
        with mock.patch.dict(os.environ, self.canned("Hing in session.py beim rsync.\n")):
            self.assertEqual(heal_fm.summarize("stack", "deadline dump"), "Hing in session.py beim rsync.")
        with mock.patch.dict(os.environ, self.canned("   ")):
            self.assertIsNone(heal_fm.summarize("stack", "deadline dump"))


HEAL_FM_CASES = (HealFMTests,)
