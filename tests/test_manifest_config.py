import os
import tempfile
import unittest
from pathlib import Path

from docsync import config, manifest
from docsync.transport import parse_rsync_stats


class ExcludeTests(unittest.TestCase):
    pats = config.DEFAULT_EXCLUDES

    def test_ds_store_anywhere(self):
        self.assertTrue(manifest.excluded("x/y/.DS_Store", self.pats))

    def test_icloud_placeholder(self):
        self.assertTrue(manifest.excluded("Inbox/.report.pdf.icloud", self.pats))

    def test_office_lock(self):
        self.assertTrue(manifest.excluded("~$budget.xlsx", self.pats))

    def test_regular_kept(self):
        self.assertFalse(manifest.excluded("Inbox/report.pdf", self.pats))

    def test_slash_pattern_prefix(self):
        self.assertTrue(manifest.excluded("Archive/2020/x.txt", ["Archive/"]))
        self.assertFalse(manifest.excluded("Other/Archive/x.txt", ["Archive/"]))


class ScanTests(unittest.TestCase):
    def test_scan_skips_symlinks_and_excludes(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "sub").mkdir()
            (root / "sub" / "a.txt").write_text("hello")
            (root / ".DS_Store").write_text("x")
            os.symlink(root / "sub" / "a.txt", root / "link.txt")
            m = manifest.scan(root, config.DEFAULT_EXCLUDES)
            self.assertEqual(set(m), {"sub/a.txt"})
            self.assertEqual(m["sub/a.txt"][1], 5)

    def test_parse_nul_records_with_newline_in_name(self):
        txt = "1\t2\t./Business/Elon Musk mode \n\n You are coach.md\x001\t3\t.DS_Store\x00"
        m = manifest.parse_lines(txt, config.DEFAULT_EXCLUDES)
        self.assertEqual(list(m), ["Business/Elon Musk mode \n\n You are coach.md"])

    def test_nfc_normalisation_unifies_both_spellings(self):
        nfd = "Perso\u0308nlich/x.pdf"
        nfc = "Pers\u00f6nlich/x.pdf"
        m = manifest.parse_lines(f"1\t2\t{nfd}\n", [])
        self.assertEqual(list(m), [nfc])
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / nfd
            p.parent.mkdir()
            p.write_text("x")
            self.assertEqual(list(manifest.scan(Path(d), [])), [nfc])

    def test_dataless_marker_hidden_and_collected(self):
        cloud = set()
        m = manifest.parse_lines("1\t-1\tevicted.pdf\x001\t5\tlocal.pdf\x00", [], cloud)
        self.assertEqual(list(m), ["local.pdf"])
        self.assertEqual(cloud, {"evicted.pdf"})

    def test_parse_lines(self):
        m = manifest.parse_lines("1700000000\t12\t./a b/c.txt\n1\t2\t.DS_Store\nbad line\n", config.DEFAULT_EXCLUDES)
        self.assertEqual(m, {"a b/c.txt": (1700000000, 12)})

    def test_save_load_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "base.json"
            manifest.save(f, {"a": (1, 2)}, {"peer": "x"})
            self.assertEqual(manifest.load(f), {"a": (1, 2)})
            self.assertIsNone(manifest.load(Path(d) / "missing.json"))


class ConfigTests(unittest.TestCase):
    def test_render_parse_roundtrip(self):
        cfg = config.Config(peers=[config.Peer("mini", ["10.42.0.1", "mini.local"], "mafoe", "~/Documents")])
        again = config.parse_config(config.render_config(cfg))
        self.assertEqual(again.peers[0].hosts, ["10.42.0.1", "mini.local"])
        self.assertEqual(again.peers[0].user, "mafoe")
        self.assertEqual(again.peer("mini").userhost("mini.local"), "mafoe@mini.local")
        self.assertEqual(again.excludes, config.DEFAULT_EXCLUDES)

    def test_bad_policy(self):
        with self.assertRaises(ValueError):
            config.parse_config('conflict_policy = "random"')

    def test_peer_needs_hosts(self):
        with self.assertRaises(ValueError):
            config.parse_config('[[peers]]\nname = "x"\nhosts = []')

    def test_maccluster_import_skips_self(self):
        toml = '''
[[nodes]]
id = "node-a"
hostnames = ["CM-AAA", "CM-AAA.local", "CM-AAA (3).local"]
ip = "10.42.0.1"
[[nodes]]
id = "node-c"
hostnames = ["mbpM5MaxMF (7).local", "mbpM5MaxMF-2.local", "mbpM5MaxMF.local"]
ip = "10.42.0.3"
'''
        peers = config.peers_from_maccluster(toml, {"mbpm5maxmf-6", "mbpm5maxmf"}, user="a321")
        self.assertEqual([p.name for p in peers], ["node-a"])
        self.assertEqual(peers[0].hosts, ["10.42.0.1", "CM-AAA.local"])
        self.assertEqual(peers[0].user, "a321")

    def test_local_hostnames_strip_suffix(self):
        self.assertIn("mbpm5maxmf", config.local_hostnames() | {"mbpm5maxmf"})


class RsyncStatsTests(unittest.TestCase):
    def test_parse(self):
        txt = """Number of files: 3 (reg: 2, dir: 1)
Number of regular files transferred: 2
Total transferred file size: 1,234 bytes
Total bytes sent: 1,400
Total bytes received: 80
"""
        self.assertEqual(parse_rsync_stats(txt), {"files": 2, "bytes": 1234, "sent": 1400, "received": 80, "skipped": 0})


if __name__ == "__main__":
    unittest.main()


class DiscoverTests(unittest.TestCase):
    def test_base_name_strips_boot_suffixes(self):
        from docsync.discover import base_name
        for h in ("CM-KWFVR7JGW3-3000.local", "CM-KWFVR7JGW3 (891)", "cm-kwfvr7jgw3.local", "CM-KWFVR7JGW3"):
            self.assertEqual(base_name(h), "cm-kwfvr7jgw3", h)
        self.assertEqual(base_name("mbpM5MaxMF-2.local"), "mbpm5maxmf")
