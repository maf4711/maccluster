"""Thunderbolt discovery, profiler parsing and cabling sketches (collected via test_manifest_config)."""
import unittest


class DiscoverTests(unittest.TestCase):
    def test_base_name_strips_boot_suffixes(self):
        from docsync.discover import base_name
        for h in ("CM-KWFVR7JGW3-3000.local", "CM-KWFVR7JGW3 (891)", "cm-kwfvr7jgw3.local", "CM-KWFVR7JGW3"):
            self.assertEqual(base_name(h), "cm-kwfvr7jgw3", h)
        self.assertEqual(base_name("mbpM5MaxMF-2.local"), "mbpm5maxmf")


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
