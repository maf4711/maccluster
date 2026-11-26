"""The docsync TOTAL block always restates what, where, and how much."""

from __future__ import annotations

import unittest

from docsync.report import PeerReport, RunReport


class TotalTransferTests(unittest.TestCase):
    def test_total_names_root_destination_and_amounts(self):
        peer = PeerReport(peer="node-b", host="mini.local", via="Thunderbolt", root="documents")
        peer.pushed_files = 2
        peer.pushed_bytes = 2048
        peer.finish()
        text = RunReport(peers=[peer]).render()
        self.assertIn("what         documents", text)
        self.assertIn("where        node-b [documents] @ mini.local via Thunderbolt", text)
        self.assertIn("how much     push 2 files  2.0 KB; pull 0 files  0.0 KB", text)

    def test_empty_run_still_states_the_three_facts(self):
        text = RunReport().render()
        self.assertIn("what         (none)", text)
        self.assertIn("where        (no peer)", text)
        self.assertIn("how much     push 0 files  0.0 KB; pull 0 files  0.0 KB", text)


if __name__ == "__main__":
    unittest.main()
