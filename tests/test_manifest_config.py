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


class PerformanceTests(unittest.TestCase):
    def test_fast_exclude_matches_reference(self):
        from docsync.manifest import _excluded_slow, excluded
        pats = config.DEFAULT_EXCLUDES + ["Archive/"]
        for rel in ("a/.DS_Store", "x/y.icloud", "~$b.xlsx", "ok/file.pdf", "Archive/2020/x", "Other/Archive/x",
                    ".docsync-partial/x", "deep/a/b/c/d.tmp", "Thumbs.db", "n.sparsebundle/bands/1"):
            self.assertEqual(excluded(rel, pats), _excluded_slow(rel, pats), rel)

    def test_split_streams_balanced(self):
        from docsync.transport import split_streams
        sizes = {f"f{i}": (10 - i) * 100 for i in range(10)}
        chunks = split_streams(list(sizes), sizes, 4)
        self.assertEqual(len(chunks), 4)
        self.assertEqual(sorted(p for c in chunks for p in c), sorted(sizes))
        loads = [sum(sizes[p] for p in c) for c in chunks]
        self.assertLess(max(loads) - min(loads), 400)
        self.assertEqual(split_streams(["a"], {"a": 1}, 4), [["a"]])

    def test_bulk_ssh_opts(self):
        from docsync.transport import BULK_SSH_OPTS, RemoteTransport
        self.assertIn("ControlPath=none", BULK_SSH_OPTS)
        self.assertTrue(any("aes128-gcm" in o for o in BULK_SSH_OPTS))
        t = RemoteTransport("u@h", "~/Documents")
        self.assertIn("ControlPath=none", t.bulk_ssh_cmd)
        self.assertNotIn("ControlPath=none", t.ssh_cmd)

    def test_bar_aggregates_streams(self):
        import io
        from docsync.progress import Bar, _sum_speed
        self.assertEqual(_sum_speed(["500.00MB/s", "0.60GB/s"]), "1.10GB/s")
        out = io.StringIO()
        b = Bar("pull ←", 100, 200 * 2**20, stream=out, enabled=True, multi=True)
        b.last_draw = 0
        b.feed("  52,428,800  50%   100.00MB/s    0:00:01 (xfr#10, to-chk=40/50)", 0)
        b.last_draw = 0
        b.feed("  52,428,800  50%   100.00MB/s    0:00:01 (xfr#10, to-chk=40/50)", 1)
        self.assertIn("50%", out.getvalue())       # 100 MB of 200 MB aggregated
        self.assertIn("200.00MB/s", out.getvalue())


class AdvisorTests(unittest.TestCase):
    HIST = """=== 2026-11-07 18:37:16
── node-b @ CM-KWFVR7JGW3 via Thunderbolt 80 Gb/s
   pushed →     1 files  5.91 GB
   deleted      local 0  remote 0  (⚠ 28 held back by deletion guard)
   ⚠ deletion guard: 28 deletions exceed max_delete=25 — skipped all deletions
══ TOTAL
   peers        1 ok, 0 failed
"""

    def test_build_prompt_contains_inputs(self):
        from docsync import advisor
        block = advisor.last_run_block(self.HIST + "\n=== 2026-11-07 18:40:00 watch: unexpected error\n")
        self.assertTrue(block.startswith("=== 2026-11-07 18:37:16"))
        p = advisor.build_prompt(block, ["a/x.conflict-T-h.gz", "a/y.conflict-T-h.conflict-U-h.gz"],
                                 ["⚠ deletion guard: 28 deletions exceed max_delete=25"], (63284, 5829))
        for needle in ("x.conflict-T-h.gz", "y.conflict-T-h.conflict-U-h.gz", "deletion guard", "63,284"):
            self.assertIn(needle, p)

    def test_rule_based_fallback_judges_by_size(self):
        from docsync import advisor
        sizes = {"a/doc.gz": 7_000, "a/doc.conflict-T-h.gz": 2_500, "a/doc.conflict-T-h.conflict-U-h.gz": 6_300}
        out = advisor.rule_based(["a/doc.conflict-T-h.gz", "a/doc.conflict-T-h.conflict-U-h.gz"],
                                 ["deletion guard: 28 deletions exceed max_delete=25"], sizes=sizes)
        text = "\n".join(out)
        self.assertIn("größte ist doc.gz (7,000 Bytes), behalten", text)
        self.assertIn("abgeschnitten: doc.conflict-T-h.gz (2,500 Bytes)", text)
        self.assertNotIn("doc.conflict-T-h.conflict-U-h.gz (6,300 Bytes)", text.split("abgeschnitten")[1] if "abgeschnitten" in text else "")
        self.assertIn("nach Größe entscheiden, nicht nach Name", text)
        self.assertIn("Umbenennung/Groß-Kleinschreibung", text)
        self.assertLessEqual(len(out), 8)

    def test_prompt_lists_sizes_and_originals(self):
        from docsync import advisor
        tree = {"a/doc.gz": (1_700_000_000, 7_000), "a/doc.conflict-T-h.gz": (1_700_000_100, 2_500)}
        p = advisor.build_prompt("", ["a/doc.conflict-T-h.gz"], [], None, tree=tree)
        self.assertIn("- a/doc.gz  (7,000 Bytes", p)
        self.assertIn("- a/doc.conflict-T-h.gz  (2,500 Bytes", p)

    def test_cli_falls_back_when_model_unavailable(self):
        import io, contextlib
        from docsync import advisor, cli
        orig = (advisor.availability, cli.load_config, cli.STATE_DIR)
        advisor.availability = lambda: (False, "device not eligible")
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "docs").mkdir()
            (Path(d) / "docs" / "f.conflict-T-h.txt").write_text("x")
            cli.load_config = lambda: config.Config(path=f"{d}/docs")
            cli.STATE_DIR = Path(d) / "state"
            buf = io.StringIO()
            try:
                with contextlib.redirect_stdout(buf):
                    rc = cli.main(["advise"])
            finally:
                advisor.availability, cli.load_config, cli.STATE_DIR = orig
        self.assertEqual(rc, 1)
        self.assertIn("rule-based fallback", buf.getvalue())
        self.assertIn("device not eligible", buf.getvalue())
        self.assertIn("f.conflict-T-h.txt", buf.getvalue())


class RootsConfigTests(unittest.TestCase):
    def test_legacy_path_is_root_documents(self):
        cfg = config.parse_config('path = "~/Docs"\n[[peers]]\nname = "a"\nhosts = ["h"]\npath = "~/PeerDocs"\n')
        roots = cfg.effective_roots()
        self.assertEqual([(r.name, r.path, r.legacy) for r in roots], [("documents", "~/Docs", True)])
        self.assertEqual(cfg.peer_path(cfg.peers[0], roots[0]), "~/PeerDocs")

    def test_roots_parse_render_roundtrip(self):
        text = ('path = "~/Documents"\n[[roots]]\nname = "desktop"\npath = "~/Desktop"\npeer_path = "~/Desk2"\n'
                'excludes = ["*.mov"]\n[[peers]]\nname = "a"\nhosts = ["h"]\n')
        cfg = config.parse_config(text)
        again = config.parse_config(config.render_config(cfg))
        self.assertEqual([r.name for r in again.effective_roots()], ["documents", "desktop"])
        d = again.root("desktop")
        self.assertEqual((d.peer_path, d.excludes), ("~/Desk2", ["*.mov"]))
        self.assertIn(".docsync*", again.for_root(d).excludes)
        self.assertEqual(again.peer_path(again.peers[0], d), "~/Desk2")

    def test_roots_without_top_level_path(self):
        cfg = config.parse_config('[[roots]]\nname = "fonts"\npath = "~/Library/Fonts"\n')
        self.assertEqual([r.name for r in cfg.effective_roots()], ["fonts"])

    def test_bad_roots(self):
        for bad in ('[[roots]]\nname = "x y"\npath = "p"\n', '[[roots]]\nname = "x"\n',
                    '[[roots]]\nname = "x"\npath = "a"\n[[roots]]\nname = "x"\npath = "b"\n'):
            with self.assertRaises(ValueError):
                config.parse_config(bad)

    def test_safetynet_settings(self):
        cfg = config.parse_config('safetynet = false\nsafetynet_keep = 3\nsafetynet_max_age_days = 7\nsafetynet_max_gb = 1.5\n')
        self.assertFalse(cfg.safetynet)
        self.assertEqual(cfg.safetynet_policy(), (3, 7.0, int(1.5 * 1024**3)))


class SafetyNetRetentionTests(unittest.TestCase):
    def _mk(self, base, days_ago, size, now):
        import time as _t
        name = _t.strftime("%Y%m%dT%H%M%SZ", _t.gmtime(now - days_ago * 86400))
        d = Path(base) / name / "Documents"
        d.mkdir(parents=True)
        (d / "f.bin").write_bytes(b"x" * size)
        return Path(base) / name

    def test_count_age_size_and_newest_kept(self):
        from docsync import safetynet
        now = 1_800_000_000
        with tempfile.TemporaryDirectory() as d:
            runs = [self._mk(d, i, 10, now) for i in range(8)]
            self.assertEqual(safetynet.prune(d, 5, None, None, now), 3)
            self.assertTrue(runs[0].exists() and not runs[7].exists())
        with tempfile.TemporaryDirectory() as d:
            new, old = self._mk(d, 40, 10, now), self._mk(d, 60, 10, now)
            self.assertEqual(safetynet.prune(d, 5, 14, None, now), 1)
            self.assertTrue(new.exists() and not old.exists())  # newest survives although older than 14 d
        with tempfile.TemporaryDirectory() as d:
            runs = [self._mk(d, i, 1000, now) for i in range(4)]
            self.assertEqual(safetynet.prune(d, 5, None, 2500, now), 2)
            self.assertEqual(safetynet.usage(d), [(runs[0].name, 1000), (runs[1].name, 1000)])
        with tempfile.TemporaryDirectory() as d:
            big = self._mk(d, 0, 5000, now)
            self.assertEqual(safetynet.prune(d, 5, None, 100, now), 0)
            self.assertTrue(big.exists())
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "notes").mkdir()
            self.assertEqual(safetynet.prune(d, 0, 0, 0, now), 0)
            self.assertTrue((Path(d) / "notes").exists())

    def test_prune_script_runs_standalone(self):
        import subprocess
        from docsync import safetynet
        now = 1_800_000_000
        with tempfile.TemporaryDirectory() as d:
            for i in range(7):
                self._mk(d, i, 10, now)
            out = subprocess.run(["python3", "-", d, "5", "-1", "-1", str(now)], input=safetynet.PRUNE_PY,
                                 text=True, capture_output=True).stdout
            self.assertIn("REMOVED 2", out)

    def test_remote_backup_script_quoting_and_output(self):
        import subprocess
        from docsync import safetynet
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "a b").mkdir()
            (Path(d) / "a b" / "it's.txt").write_text("hello")
            script = safetynet.remote_backup_script(d, ["a b/it's.txt", "missing.txt"], "20261107T120000Z")
            res = subprocess.run(["sh", "-s"], input=script, text=True, capture_output=True)
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertEqual(safetynet.parse_backup_output(res.stdout), (1, 5))
            self.assertEqual((Path(d) / ".docsync-safetynet/20261107T120000Z/a b/it's.txt").read_text(), "hello")


class FidelityTests(unittest.TestCase):
    PROBE = ("PATH /opt/homebrew/bin/rsync\nrsync  version 3.5.2  protocol version 33\n    ACLs,\n    xattrs, crtimes\nEND\n"
             "PATH /usr/bin/rsync\nopenrsync: protocol version 29\nEND\n")

    def test_probe_and_choose(self):
        from docsync import fidelity
        entries = fidelity.parse_probe(self.PROBE)
        self.assertEqual(entries[1], ("/usr/bin/rsync", set()))
        path, flags, warn = fidelity.choose({"xattrs", "acls", "crtimes"}, entries)
        self.assertEqual(path, "/opt/homebrew/bin/rsync")
        self.assertIn("-X", flags); self.assertIn("-A", flags); self.assertIn("--crtimes", flags)
        self.assertIn("--filter=-x com.apple.provenance", flags)
        self.assertEqual(warn, "")

    def test_openrsync_only_peer_warns(self):
        from docsync import fidelity
        path, flags, warn = fidelity.choose({"xattrs", "acls", "crtimes"}, [("/usr/bin/rsync", set())])
        self.assertEqual(flags, [])
        self.assertEqual(warn, "peer rsync /usr/bin/rsync lacks acls/crtimes/xattrs — synced without them")
        self.assertIn("this Mac's rsync lacks xattrs", fidelity.choose({"acls", "crtimes"}, fidelity.parse_probe(self.PROBE))[2])

    def test_probe_script_sees_capabilities_beyond_line_six(self):
        import subprocess
        from docsync import fidelity
        entries = fidelity.parse_probe(subprocess.run(["sh", "-c", fidelity.PROBE_SH], text=True, capture_output=True).stdout)
        best = max(entries, key=lambda e: len(e[1]))
        if best[0].startswith("/opt/homebrew") or best[0].startswith("/usr/local"):
            self.assertEqual(best[1], {"xattrs", "acls", "crtimes"})
        self.assertEqual(fidelity.choose(set(), [])[2][:25], "peer rsync not found in /")


class LaunchdEnvironmentTests(unittest.TestCase):
    def test_system_tools_are_called_with_absolute_paths(self):
        """launchd's PATH lacks /sbin and /usr/sbin: bare 'ping' made every agent run report
        'none answered a ping' (2026-11-07)."""
        import re
        root = Path(__file__).resolve().parent.parent / "docsync"
        bare = re.compile(r'\[\s*"(ping|ifconfig|arp|networksetup|system_profiler|sysctl|dns-sd|pmset|osascript)"')
        for f in root.glob("*.py"):
            self.assertIsNone(bare.search(f.read_text()), f.name)

    def test_plist_path_has_sbin(self):
        from docsync.agent import plist_xml
        self.assertIn(":/usr/sbin:/sbin<", plist_xml(60, "/x.py"))
