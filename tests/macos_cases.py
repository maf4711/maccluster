"""macOS helpers: pmset, launchd plist, run report (collected via test_manifest_config)."""
import tempfile
import unittest
from pathlib import Path


class MacosTests(unittest.TestCase):
    def test_pmset_parse(self):
        from docsync.macos import parse_pmset_batt
        self.assertFalse(parse_pmset_batt("Now drawing from 'AC Power'\n -InternalBattery-0 (id=1)\t100%; charged"))
        self.assertTrue(parse_pmset_batt("Now drawing from 'Battery Power'\n -InternalBattery-0 (id=1)\t83%; discharging"))
        self.assertIsNone(parse_pmset_batt(""))

    def test_agent_plist_has_watchpaths_and_lints(self):
        import shutil, subprocess
        from docsync.agent import plist_xml
        xml = plist_xml(900, "/x/docsync-launchd.py", "/Users/me/Documents")
        self.assertIn("<key>WatchPaths</key><array><string>/Users/me/Documents</string></array>", xml)
        self.assertIn("<string>--from-agent</string>", xml)
        self.assertNotIn("WatchPaths", plist_xml(900, "/x/e.py", None))
        if shutil.which("plutil"):
            with tempfile.TemporaryDirectory() as d:
                f = Path(d) / "t.plist"
                f.write_text(xml)
                self.assertEqual(subprocess.run(["plutil", "-lint", str(f)], capture_output=True).returncode, 0)

    def test_agent_plist_has_max_runtime(self):
        from docsync.agent import plist_xml
        self.assertIn("<string>--max-runtime</string><string>1800</string>", plist_xml(900, "/x/e.py", None))

    def test_run_report_notable(self):
        from docsync.report import PeerReport, RunReport
        r = RunReport()
        self.assertEqual(r.notable(), "")
        p = PeerReport(peer="x"); p.conflicts.append(("a", "remote", "a.conflict")); p.deletes_skipped = 30
        q = PeerReport(peer="y"); q.ok = False
        r.peers += [p, q]
        self.assertEqual(r.notable(), "failed: y; 1 conflict; 30 deletions held back")
