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


NDP_SAMPLE = """\
fe80::1%lo0                             (incomplete)         lo0 permanent R
fe80::aabb:ccdd:eeff:1%bridge0          36:bf:cb:ca:26:40    bridge0 permanent R
fe80::1122:3344:5566:7788%bridge0       36:8e:95:56:73:c0    bridge0 3s        D
fe80::9999:8888:7777:6666%en0           ac:bc:b5:d1:b4:9e    en0 20s       R  R
"""


class LinkLocalTests(unittest.TestCase):
    def test_parse_ndp_keeps_only_other_host_on_tb_bridge(self):
        from docsync.discover import parse_ndp_neighbours
        got = parse_ndp_neighbours(NDP_SAMPLE, {"bridge0"}, {"fe80::aabb:ccdd:eeff:1%bridge0"})
        self.assertEqual(got, ["fe80::1122:3344:5566:7788%bridge0"])

    def test_rsync_spec_brackets_ipv6_link_local(self):
        from docsync.transport import rsync_spec
        scoped = "fe80::1122:3344:5566:7788%bridge0"
        self.assertEqual(rsync_spec(f"a321@{scoped}", "Documents"),
                         f"a321@[{scoped}]:Documents/")
        self.assertEqual(rsync_spec("a321@10.42.0.1", "Documents"), "a321@10.42.0.1:Documents/")

    def test_resolve_uses_tb_link_local_before_lan(self):
        from docsync import discover
        from docsync.config import Peer
        from docsync.hosts import resolve
        from docsync.transport import RemoteTransport
        peer = Peer("node-a", ["10.42.0.1", "CM-CFMQ2D029F.local"], user="a321")
        calls = []
        orig = (discover.order_hosts, discover.ping, discover.tb_link_locals,
                discover.tb_neighbours, discover.bonjour_fallback, RemoteTransport.ssh)
        discover.order_hosts = lambda hosts: [("10.42.0.1", "Thunderbolt (link down)"),
                                               ("CM-CFMQ2D029F.local", "LAN")]

        def ping(host, *a, **k):
            calls.append(("ping", host))
            return 1.0 if str(host).endswith(".local") else None

        def ssh(self, cmd, stdin=None, timeout=120):
            calls.append(("ssh", self.userhost))
            class R:
                stdout = "CM-CFMQ2D029F\n"
                returncode = 0
            return R()

        discover.ping = ping
        discover.tb_link_locals = lambda: ["fe80::1122:3344:5566:7788%bridge0"]
        discover.tb_neighbours = lambda: ["169.254.231.86"]
        discover.bonjour_fallback = lambda *a, **k: self.fail("Bonjour must not run before the TB neighbour")
        RemoteTransport.ssh = ssh
        try:
            host, via = resolve(peer, lambda *a: None)
        finally:
            (discover.order_hosts, discover.ping, discover.tb_link_locals,
             discover.tb_neighbours, discover.bonjour_fallback, RemoteTransport.ssh) = orig
        self.assertEqual((host, via), ("fe80::1122:3344:5566:7788%bridge0", "Thunderbolt (link-local)"))
        self.assertEqual(calls[0], ("ssh", "a321@fe80::1122:3344:5566:7788%bridge0"))
        self.assertNotIn(("ping", "CM-CFMQ2D029F.local"), calls)

    def test_parse_dns_sd_aaaa_keeps_tb_link_local_only(self):
        from docsync.discover import parse_dns_sd_aaaa
        sample = (
            "17:25:07.136  Add  2             13  CM-CFMQ2D029F.local.                   "
            "FE80:0000:0000:0000:14FF:7D24:3825:757E%bridge0 120\n"
            "17:25:07.200  Add  2             14  CM-CFMQ2D029F.local.                   "
            "FE80:0000:0000:0000:AAAA:BBBB:CCCC:DDDD%en0 120\n"
        )
        self.assertEqual(parse_dns_sd_aaaa(sample, {"bridge0"}),
                         ["fe80::14ff:7d24:3825:757e%bridge0"])

    def test_resolve_uses_tb_mdns_when_neighbour_cache_is_empty(self):
        from docsync import discover
        from docsync.config import Peer
        from docsync.hosts import resolve
        from docsync.transport import RemoteTransport
        peer = Peer("node-a", ["10.42.0.1", "CM-CFMQ2D029F.local"], user="a321")
        calls = []
        orig = (discover.order_hosts, discover.ping, discover.tb_link_locals, discover.tb_hostname_addrs,
                discover.tb_neighbours, discover.bonjour_fallback, RemoteTransport.ssh)
        discover.order_hosts = lambda hosts: [("10.42.0.1", "Thunderbolt (link down)"),
                                               ("CM-CFMQ2D029F.local", "LAN")]
        discover.ping = lambda host, *a, **k: 1.0 if str(host).endswith(".local") else None
        discover.tb_link_locals = lambda: []
        discover.tb_hostname_addrs = lambda hosts: ["fe80::14ff:7d24:3825:757e%bridge0"]
        discover.tb_neighbours = lambda: []
        discover.bonjour_fallback = lambda *a, **k: self.fail("Bonjour")

        def ssh(self, cmd, stdin=None, timeout=120):
            calls.append(self.userhost)
            class R:
                stdout = "CM-CFMQ2D029F\n"
                returncode = 0
            return R()

        RemoteTransport.ssh = ssh
        try:
            host, via = resolve(peer, lambda *a: None)
        finally:
            (discover.order_hosts, discover.ping, discover.tb_link_locals, discover.tb_hostname_addrs,
             discover.tb_neighbours, discover.bonjour_fallback, RemoteTransport.ssh) = orig
        self.assertEqual((host, via), ("fe80::14ff:7d24:3825:757e%bridge0", "Thunderbolt (link-local)"))
        self.assertEqual(calls, ["a321@fe80::14ff:7d24:3825:757e%bridge0"])

    def test_bonjour_browse_reads_companion_link_instance_on_iface(self):
        from docsync import discover
        line = ("18:22:57.159  Add        2  19 local.               "
                "_companion-link._tcp. mbpM5MaxMF (8)\n")
        seen = []

        class Proc:
            def terminate(self):
                pass

            def kill(self):
                pass

            def communicate(self, timeout=2):
                return line, ""

        def popen(cmd, **k):
            seen.append(cmd)
            return Proc()

        orig = discover.subprocess.Popen
        discover.subprocess.Popen = popen
        try:
            got = discover.bonjour_ssh_hosts(0, iface="bridge0", service="_companion-link._tcp")
        finally:
            discover.subprocess.Popen = orig
        self.assertEqual(got, ["mbpM5MaxMF (8)"])
        self.assertIn("-i", seen[0])
        self.assertIn("bridge0", seen[0])
        self.assertIn("_companion-link._tcp", seen[0])

    def test_tb_hostname_addrs_uses_later_name_when_first_is_stale(self):
        from docsync import discover
        sample = (
            "18:08:07.849  Add  40000002      19  mbpM5MaxMF.local.                    "
            "FE80:0000:0000:0000:00F5:F708:7C0D:F861%bridge0 4500\n"
        )
        calls = []
        orig = (discover.tb_interfaces, discover.iface_ipv4, discover.iface_ipv6_link_locals,
                discover._dns_sd_getaddr, discover.bonjour_ssh_hosts)
        discover.tb_interfaces = lambda: ["bridge0"]
        discover.iface_ipv4 = lambda dev: []
        discover.iface_ipv6_link_locals = lambda dev: ["fe80::1%bridge0"]

        def getaddr(host, iface, seconds):
            calls.append(host)
            return sample if host == "mbpM5MaxMF.local" else ""

        discover._dns_sd_getaddr = getaddr
        discover.bonjour_ssh_hosts = lambda *a, **k: self.fail("browse must not run after a configured name answers")
        try:
            got = discover.tb_hostname_addrs(
                ["10.42.0.3", "mbpM5MaxMF-2.local", "mbpm5maxmf-2.local", "mbpM5MaxMF.local"])
        finally:
            (discover.tb_interfaces, discover.iface_ipv4, discover.iface_ipv6_link_locals,
             discover._dns_sd_getaddr, discover.bonjour_ssh_hosts) = orig
        self.assertEqual(got, ["fe80::f5:f708:7c0d:f861%bridge0"])
        self.assertEqual(calls, ["mbpM5MaxMF-2.local", "mbpM5MaxMF.local"])

    def test_tb_hostname_addrs_uses_live_bonjour_name_on_tb_iface(self):
        from docsync import discover
        sample = (
            "18:08:07.849  Add  40000002      19  mbpM5MaxMF-6.local.                  "
            "FE80:0000:0000:0000:00F5:F708:7C0D:F861%bridge0 4500\n"
        )
        calls = []
        browsed = []
        orig = (discover.tb_interfaces, discover.iface_ipv4, discover.iface_ipv6_link_locals,
                discover._dns_sd_getaddr, discover.bonjour_ssh_hosts, discover.resolve_instance)
        discover.tb_interfaces = lambda: ["bridge0"]
        discover.iface_ipv4 = lambda dev: []
        discover.iface_ipv6_link_locals = lambda dev: ["fe80::1%bridge0"]

        def getaddr(host, iface, seconds):
            calls.append((host, iface))
            return sample if host == "mbpM5MaxMF-6.local" else ""

        def browse(seconds=3.0, iface=None, service="_ssh._tcp"):
            browsed.append((iface, service))
            if iface == "bridge0" and service == "_companion-link._tcp":
                return ["mbpM5MaxMF (8)"]
            return []

        def resolve(inst, seconds=3.0, service="_ssh._tcp"):
            self.assertEqual((inst, service), ("mbpM5MaxMF (8)", "_companion-link._tcp"))
            return "mbpM5MaxMF-6.local"

        discover._dns_sd_getaddr = getaddr
        discover.bonjour_ssh_hosts = browse
        discover.resolve_instance = resolve
        try:
            got = discover.tb_hostname_addrs(["10.42.0.3", "mbpM5MaxMF-2.local", "mbpM5MaxMF.local"])
        finally:
            (discover.tb_interfaces, discover.iface_ipv4, discover.iface_ipv6_link_locals,
             discover._dns_sd_getaddr, discover.bonjour_ssh_hosts, discover.resolve_instance) = orig
        self.assertEqual(got, ["fe80::f5:f708:7c0d:f861%bridge0"])
        self.assertEqual(browsed, [("bridge0", "_companion-link._tcp")])
        self.assertIn(("mbpM5MaxMF-6.local", "bridge0"), calls)


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
