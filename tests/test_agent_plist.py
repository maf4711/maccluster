"""launchd plists: no WatchPaths trigger on iCloud Desktop & Documents roots, a 900 s throttle where
WatchPaths stay, background priority (Nice 10) for both agents, and the hold file that blocks every
agent load. Incident 2026-11-25: WatchPaths on ~/Documents and ~/Desktop (constantly touched by
iCloud) with ThrottleInterval 60 meant a full sync every ~11 min; a manual bootout was undone by
heal's reload_agent. No real launchctl, no real HOME: subprocess and HOME are patched."""
import os
import plistlib
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from docsync import agent, heal, icloud
from docsync.heal_rules import Context


def parse(xml: str) -> dict:
    return plistlib.loads(xml.encode())


class AgentPlistTests(unittest.TestCase):
    def test_icloud_root_gets_no_watchpaths(self):
        paths = ["/tmp/x/Documents", "/tmp/x/Desktop"]
        with mock.patch("docsync.icloud.is_icloud_backed", return_value=True) as det:
            p = parse(agent.plist_xml(15 * 60, "/x/e.py", paths))
        self.assertNotIn("WatchPaths", p)
        self.assertNotIn("ThrottleInterval", p)
        self.assertEqual(p["StartInterval"], 900)
        self.assertEqual(sorted(c.args[0] for c in det.call_args_list), sorted(paths))
        # a mixed list keeps only the non-iCloud path
        with mock.patch("docsync.icloud.is_icloud_backed", side_effect=lambda q: q.endswith("Documents")):
            p = parse(agent.plist_xml(900, "/x/e.py", ["/tmp/x/Documents", "/tmp/dev/llm"]))
        self.assertEqual(p["WatchPaths"], ["/tmp/dev/llm"])

    def test_is_icloud_backed_walks_ancestors(self):
        home = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(lambda: os.rmdir(home))
        seen = []

        def dd(name, path):
            seen.append(str(path))
            return str(path) == f"{home}/Documents"
        with mock.patch.dict(os.environ, {"HOME": str(home)}), mock.patch("docsync.icloud.dd_on", side_effect=dd):
            self.assertTrue(icloud.is_icloud_backed(f"{home}/Documents"))
            self.assertTrue(icloud.is_icloud_backed(f"{home}/Documents/Projekte/sub"))
            self.assertFalse(icloud.is_icloud_backed(f"{home}/Developer/llm-claude"))
            seen.clear()
            icloud.is_icloud_backed(f"{home}/Developer/x")
        self.assertEqual(seen, [f"{home}/Developer/x", f"{home}/Developer", str(home)])  # stops at $HOME

    def test_watchpaths_throttle_is_at_least_900(self):
        with mock.patch("docsync.icloud.is_icloud_backed", return_value=False):
            for interval in (60, 900, 3600):
                p = parse(agent.plist_xml(interval, "/x/e.py", "/Users/me/Developer/llm-claude"))
                self.assertEqual(p["WatchPaths"], ["/Users/me/Developer/llm-claude"])
                self.assertGreaterEqual(p["ThrottleInterval"], 900)

    def test_plists_run_at_background_priority(self):
        for xml in (agent.plist_xml(900, "/x/e.py", None), agent.heal_plist_xml(15), agent.heal_plist_xml(15, True)):
            p = parse(xml)
            self.assertEqual(p["Nice"], 10)
            self.assertIs(p["LowPriorityIO"], True)
            self.assertEqual(p["ProcessType"], "Background")

    def test_detection_error_means_no_watchpaths(self):
        for err in (OSError("getxattr"), RuntimeError("no libc")):
            with mock.patch("docsync.icloud.is_icloud_backed", side_effect=err):
                p = parse(agent.plist_xml(900, "/x/e.py", ["/tmp/dev/llm", "/tmp/x/Documents"]))
            self.assertNotIn("WatchPaths", p)
            self.assertEqual(p["StartInterval"], 900)


class HoldFileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        (self.home / ".cache").mkdir()
        (self.home / ".cache" / "docsync.hold").write_text("incident\n")
        self.calls = []

        def record(argv, *a, **kw):
            self.calls.append(list(argv))
            raise AssertionError(f"subprocess while held: {argv}")
        for p in (mock.patch.dict(os.environ, {"HOME": str(self.home)}),
                  mock.patch("subprocess.run", side_effect=record),
                  mock.patch("subprocess.Popen", side_effect=record),
                  mock.patch.object(agent, "PLIST", self.home / "LA" / "sync.plist"),
                  mock.patch.object(agent, "HEAL_PLIST", self.home / "LA" / "heal.plist")):
            p.start()
            self.addCleanup(p.stop)

    def healer(self, plist: Path, runner):
        ctx = Context(node="node-a", coordinator=False, cluster_nodes={}, python="/x/python3",
                      state_dir=self.home / "state", log_dir=self.home / "logs", maccluster="/bin/maccluster",
                      maccluster_src=self.home / "src", plist=plist, uid=501)
        (self.home / "state").mkdir(exist_ok=True)
        return heal.Healer(ctx, runner, now=lambda: 1_800_000_000.0, sleep=lambda s: None,
                           alive=lambda pid: False, send_wol=lambda m: None, use_fm=False)

    def test_hold_file_blocks_every_load(self):
        self.assertIn("held: incident", agent.install(15, ["/tmp/dev/llm"]))
        self.assertIn("held: incident", agent.install_heal(15, True))
        self.assertFalse((self.home / "LA").exists())  # no plist written either
        ran = []

        def runner(argv, timeout=60, env=None, detach=False, cwd=None):
            ran.append(list(argv))
            return 0, ""
        for plist in (self.home / "present.plist", self.home / "missing.plist"):
            if plist.name == "present.plist":
                plist.write_text("<plist/>")
            status, text = self.healer(plist, runner).act_reload_agent({})
            self.assertEqual(status, "skip")
            self.assertIn("held: incident", text)
        bad = [a for a in ran + self.calls if any("bootstrap" in x or "install-agent" in x for x in a)]
        self.assertEqual(bad, [])
        self.assertEqual(self.calls, [])

    def test_empty_hold_file_still_holds(self):
        (self.home / ".cache" / "docsync.hold").write_text("")
        self.assertIn("held: ", agent.install(15))
        self.assertEqual(self.calls, [])
