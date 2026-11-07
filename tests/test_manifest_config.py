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


class ProgressTests(unittest.TestCase):
    def test_parse_progress2_line(self):
        from docsync.progress import parse_progress
        p = parse_progress("  1,234,567  45%   12.34MB/s    0:00:12 (xfr#3, to-chk=100/370)")
        self.assertEqual((p["bytes"], p["pct"], p["speed"], p["eta"], p["xfr"], p["left"], p["total"]),
                         (1234567, 45, "12.34MB/s", "0:00:12", 3, 100, 370))
        self.assertIsNone(parse_progress("Number of files: 3"))
        self.assertEqual(parse_progress("999 100%  1.00kB/s 0:00:00")["xfr"], None)

    def test_bar_writes_to_stream_and_finishes(self):
        import io
        from docsync.progress import Bar
        out = io.StringIO()
        b = Bar("pull ←", 370, 11_000_000, stream=out, enabled=True)
        b.last_draw = 0
        b.feed("  5,500,000  50%   12.34MB/s    0:00:12 (xfr#3, to-chk=185/370)")
        b.finish(11_000_000, 370)
        text = out.getvalue()
        self.assertIn("50%", text)
        self.assertIn("185/370 files", text)
        self.assertIn("5,371/10,742 KB", text)
        self.assertIn("12.34MB/s", text)
        self.assertIn("100%", text)
        self.assertTrue(text.endswith("\n"))


class ThunderboltTests(unittest.TestCase):
    def test_is_tb_host(self):
        import ipaddress
        from docsync.discover import is_tb_host
        active = [ipaddress.IPv4Network("10.42.0.0/24")]
        conf = ipaddress.IPv4Network("10.42.0.0/24")
        self.assertEqual(is_tb_host("10.42.0.1", active, conf), "active")
        self.assertEqual(is_tb_host("10.42.0.1", [], conf), "down")
        self.assertEqual(is_tb_host("169.254.31.43", [], None), "down")
        self.assertEqual(is_tb_host("192.168.178.94", active, conf), "")
        self.assertEqual(is_tb_host("mini.local", active, conf), "")

    def test_order_hosts_tb_first_dead_last(self):
        import ipaddress
        from docsync import discover
        orig = (discover.tb_networks, discover.cluster_tb_subnet)
        discover.tb_networks = lambda: [ipaddress.IPv4Network("10.42.0.0/24")]
        discover.cluster_tb_subnet = lambda: ipaddress.IPv4Network("10.42.0.0/24")
        try:
            got = discover.order_hosts(["mini.local", "169.254.9.9", "10.42.0.1"])
        finally:
            discover.tb_networks, discover.cluster_tb_subnet = orig
        self.assertEqual(got, [("10.42.0.1", "Thunderbolt"), ("mini.local", "LAN"),
                               ("169.254.9.9", "Thunderbolt (link down)")])


class ThunderboltProfilerTests(unittest.TestCase):
    SAMPLE = """Thunderbolt/USB4:

    Thunderbolt/USB4 Bus 1:

      Vendor Name: Apple Inc.
      Device Name: MacBook Pro
      UID: 0x05AC20D5BAF28991
      Port:
          Status: No device connected
          Link Status: 0x100
          Speed: Up to 120 Gb/s
          Receptacle: 2

    Thunderbolt/USB4 Bus 0:

      Vendor Name: Apple Inc.
      Device Name: MacBook Pro
      UID: 0x05AC20D5BAF28990
      Port:
          Status: Device connected
          Link Status: 0x2
          Speed: Up to 40 Gb/s
          Receptacle: 1
          Micro Firmware Version: 0.0.0

        Mac mini:

          Vendor Name: Apple Inc.
          Device Name: Mac16,11
          Device ID: 0xA
          Domain UUID: 4EDF8877-19BE-4DA4-A094-EFB6847D77E6
          Services:
            Internet Protocol:
              Protocol ID: 1
"""

    def test_ports_speed_and_device(self):
        from docsync.thunderbolt import TBState, parse_profiler_text
        ports = parse_profiler_text(self.SAMPLE)
        self.assertEqual(len(ports), 2)
        idle, up = ports
        self.assertFalse(idle.connected)
        self.assertEqual((idle.speed_gbps, idle.receptacle), (120, "2"))
        self.assertTrue(up.connected)
        self.assertEqual((up.speed_gbps, up.receptacle, up.device, up.device_model), (40, "1", "Mac mini", "Mac16,11"))
        self.assertEqual(up.device_domain, "4EDF8877-19BE-4DA4-A094-EFB6847D77E6")
        st = TBState(ports=ports, active_ifaces={"bridge0": ["10.42.0.3"]})
        self.assertTrue(st.link_up)
        self.assertEqual(st.label(), "Thunderbolt 40 Gb/s")
        self.assertEqual(TBState(ports=ports).label(), "Thunderbolt cable attached, bridge has no IP")
        self.assertEqual(TBState(ports=[idle], interfaces=["bridge0"]).label(), "no Thunderbolt link")


class SketchTests(unittest.TestCase):
    def test_remote_parse_and_sketch_marks_front_ports_and_free_tb(self):
        from docsync import tbsketch
        out = "Mac16,11\n=== TB\n" + ThunderboltProfilerTests.SAMPLE + "=== BRIDGE\n\tinet 10.42.0.2 netmask 0xffffff00 broadcast 10.42.0.255\n\tstatus: active\n"
        model, st = tbsketch.parse_remote(out)
        self.assertEqual(model, "Mac16,11")
        self.assertEqual(st.active_ifaces, {"bridge0": ["10.42.0.2"]})
        sk = tbsketch.sketch_mac("node-b", model, st, highlight_free=True)
        self.assertIn("Mac mini (M4 Pro)", sk)
        self.assertIn("TB1: ", sk)
        self.assertIn("Mac mini", sk)
        self.assertIn("40 Gb/s", sk)
        self.assertIn("TB2: free ◀ plug here", sk)
        self.assertIn("front: (USB-C ✗ no TB)  (USB-C ✗ no TB)", sk)
        self.assertIn("bridge0: 10.42.0.2", sk)

    def test_wrong_right_sketch(self):
        from docsync import tbsketch
        sk = tbsketch.sketch_wrong_right("mbp", "node-b", "Mac16,11")
        self.assertIn("✗ wrong", sk)
        self.assertIn("node-b FRONT port", sk)
        self.assertIn("node-b BACK port (TB1-3)", sk)


class RemoteRemoveScriptTests(unittest.TestCase):
    def test_script_runs_locally_with_spaces_and_quotes(self):
        import subprocess
        from docsync.transport import remote_remove_script
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "Documents"
            (root / "_Archiv/2026").mkdir(parents=True)
            f = root / "_Archiv/2026/a b's 2.tar.conflict-x.gz"
            f.write_text("x")
            script = remote_remove_script(str(root), ["_Archiv/2026/a b's 2.tar.conflict-x.gz"], "T1", True)
            res = subprocess.run(["sh", "-c", script], env={"HOME": d, "PATH": "/bin:/usr/bin"}, capture_output=True, text=True)
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertFalse(f.exists())
            self.assertTrue((Path(d) / ".Trash/docsync-T1/_Archiv/2026/a b's 2.tar.conflict-x.gz").exists())
            self.assertNotIn("\"$T/", script)


class RemotePathTests(unittest.TestCase):
    def test_tilde_expands_via_home(self):
        import subprocess
        from docsync.transport import remote_path
        self.assertEqual(remote_path("~/Documents"), '"$HOME"/Documents')
        self.assertEqual(remote_path("~/My Docs"), '"$HOME"/\'My Docs\'')
        self.assertEqual(remote_path("/Volumes/X/Docs"), "/Volumes/X/Docs")
        self.assertEqual(remote_path("~"), '"$HOME"')
        out = subprocess.run(["sh", "-c", f"echo {remote_path('~/Documents')}"], env={"HOME": "/h"}, capture_output=True, text=True).stdout.strip()
        self.assertEqual(out, "/h/Documents")


class SpeedtestTests(unittest.TestCase):
    def test_tcp_loopback_roundtrip(self):
        """Run the peer server locally (sh -c python3 - instead of ssh) and measure over loopback."""
        from docsync import speedtest
        fake_ssh = ["sh", "-c", "exec $2", "sh"]  # argv: sh python3(userhost) "python3 - PORT SECS"(cmd) → word-split and exec
        up, down = speedtest.tcp_test(fake_ssh, "local", "127.0.0.1", 0.5)
        self.assertGreater(up, 50 * 2**20)
        self.assertGreater(down, 50 * 2**20)

    def test_render_and_summary(self):
        from docsync import speedtest
        r = speedtest.Result("node-a", "10.42.0.1", "Thunderbolt 80 Gb/s", 0.8, 1e9, 1.2e9, 5e8, 6e8)
        txt = r.render() + speedtest.summary([r])
        self.assertIn("TCP raw", txt)
        self.assertIn("Gb/s", txt)
        self.assertIn("fastest: node-a", txt)


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

    def test_run_report_notable(self):
        from docsync.report import PeerReport, RunReport
        r = RunReport()
        self.assertEqual(r.notable(), "")
        p = PeerReport(peer="x"); p.conflicts.append(("a", "remote", "a.conflict")); p.deletes_skipped = 30
        q = PeerReport(peer="y"); q.ok = False
        r.peers += [p, q]
        self.assertEqual(r.notable(), "failed: y; 1 conflict; 30 deletions held back")
