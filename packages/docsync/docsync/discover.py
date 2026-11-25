"""Find SSH-capable Macs on the LAN via Bonjour (dns-sd) and probe peers."""

from __future__ import annotations

import ipaddress
import re
import subprocess
import time
from pathlib import Path


def bonjour_ssh_hosts(seconds: float = 3.0, iface: str | None = None, service: str = "_ssh._tcp") -> list[str]:
    """Browse a Bonjour service for a few seconds, return instance names (e.g. 'CM-KWFVR7JGW3').

    `iface` limits the query to one interface (`dns-sd -i bridge0`), so a Thunderbolt
    browse does not pick up a peer that is only on Wi-Fi."""
    cmd = ["/usr/bin/dns-sd"]
    if iface:
        cmd += ["-i", iface]
    cmd += ["-B", service, "local"]
    try:
        proc = subprocess.Popen(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    except FileNotFoundError:
        return []
    found: list[str] = []
    deadline = time.monotonic() + seconds
    try:
        while time.monotonic() < deadline:
            time.sleep(0.2)
        proc.terminate()
        out, _ = proc.communicate(timeout=2)
    except Exception:
        proc.kill()
        return found
    for line in out.splitlines():
        m = re.search(rf"{re.escape(service)}\.\s+(.+?)\s*$", line)
        if m and " Add " in line:
            name = m.group(1).strip()
            if name not in found:
                found.append(name)
    return found


def to_local_hostname(instance: str) -> str:
    """'CM-KWFVR7JGW3 (720)' -> 'CM-KWFVR7JGW3.local'."""
    base = re.sub(r"\s*\(\d+\)\s*$", "", instance).strip()
    return f"{base}.local"


def resolve_instance(instance: str, seconds: float = 3.0, service: str = "_ssh._tcp") -> str | None:
    """dns-sd -L: Bonjour instance -> the host name it can be reached at ('CM-X-3000.local')."""
    try:
        proc = subprocess.Popen(["/usr/bin/dns-sd", "-L", instance, service, "local"], text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    except FileNotFoundError:
        return None
    deadline = time.monotonic() + seconds
    try:
        while time.monotonic() < deadline:
            time.sleep(0.2)
        proc.terminate()
        out, _ = proc.communicate(timeout=2)
    except Exception:
        proc.kill()
        return None
    m = re.search(r"can be reached at ([^\s:]+?)\.?:\d+", out)
    return m.group(1) if m else None


def base_name(host: str) -> str:
    """'CM-KWFVR7JGW3-3000.local' / 'CM-KWFVR7JGW3 (891)' -> 'cm-kwfvr7jgw3'."""
    h = host.lower().replace(".local", "")
    h = re.sub(r"\s*\(\d+\)\s*$", "", h)
    h = re.sub(r"-\d+$", "", h)
    return h.strip()


def bonjour_fallback(hosts: list[str], seconds: float = 3.0) -> str | None:
    """Macs re-advertise as NAME-2930, NAME-3000, ... after each boot. Find a Bonjour
    _ssh instance whose base name matches one of the configured hosts and resolve it."""
    wanted = {base_name(h) for h in hosts if not h.replace(".", "").isdigit()}
    for inst in bonjour_ssh_hosts(seconds):
        if base_name(inst) in wanted:
            host = resolve_instance(inst)
            if host and ping(host) is not None:
                return host
    return None


def ping(host: str, timeout_ms: int = 800) -> float | None:
    """Round-trip ms or None."""
    try:
        res = subprocess.run(["/sbin/ping", "-c", "1", "-W", str(timeout_ms), host],
                             text=True, capture_output=True, timeout=timeout_ms / 1000 + 2)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None
    m = re.search(r"time=([\d.]+) ms", res.stdout)
    return float(m.group(1)) if res.returncode == 0 and m else None


def first_reachable(hosts: list[str]) -> tuple[str | None, dict[str, float | None]]:
    probes: dict[str, float | None] = {}
    for h in hosts:
        probes[h] = ping(h)
        if probes[h] is not None:
            return h, probes
    return None, probes


# ── Thunderbolt detection (like maccluster: TB bridge first, then LAN) ──────────

def tb_interfaces() -> list[str]:
    """Devices whose hardware port is Thunderbolt (en1, en6, …) plus the Thunderbolt Bridge (bridge0)."""
    try:
        out = subprocess.run(["/usr/sbin/networksetup", "-listallhardwareports"], text=True, capture_output=True, timeout=5).stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []
    devs, port = [], ""
    for line in out.splitlines():
        if line.startswith("Hardware Port:"):
            port = line.split(":", 1)[1].strip()
        elif line.startswith("Device:") and "thunderbolt" in port.lower():
            devs.append(line.split(":", 1)[1].strip())
    return devs


def iface_ipv4(dev: str) -> list[ipaddress.IPv4Interface]:
    """Active IPv4 addresses of an interface (empty when down / no address)."""
    try:
        out = subprocess.run(["/sbin/ifconfig", dev], text=True, capture_output=True, timeout=5).stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []
    if re.search(r"status: inactive", out):
        return []
    res = []
    for m in re.finditer(r"inet (\d+\.\d+\.\d+\.\d+) netmask 0x([0-9a-f]{8})", out):
        bits = bin(int(m.group(2), 16)).count("1")
        res.append(ipaddress.IPv4Interface(f"{m.group(1)}/{bits}"))
    return res


def tb_networks() -> list[ipaddress.IPv4Network]:
    """Subnets reachable over an active Thunderbolt link right now."""
    nets = []
    for dev in tb_interfaces():
        for itf in iface_ipv4(dev):
            nets.append(itf.network)
    return nets


def cluster_tb_subnet() -> ipaddress.IPv4Network | None:
    """maccluster's configured TB subnet (10.42.0.0/24) — hosts in it are TB candidates even
    when the bridge is down, so we can tag them (and skip them fast) instead of guessing."""
    f = Path("~/.config/maccluster/cluster.toml").expanduser()
    if not f.exists():
        return None
    m = re.search(r'^subnet\s*=\s*"([^"]+)"', f.read_text(), re.M)
    try:
        return ipaddress.IPv4Network(m.group(1)) if m else None
    except ValueError:
        return None


def is_tb_host(host: str, active: list[ipaddress.IPv4Network], configured: ipaddress.IPv4Network | None) -> str:
    """'active' if host sits in a live TB subnet, 'down' if it is a TB address with no live link, '' otherwise."""
    try:
        ip = ipaddress.IPv4Address(host)
    except ValueError:
        return ""
    if any(ip in n for n in active):
        return "active"
    if ip.is_link_local or (configured and ip in configured):
        return "down"
    return ""


def tb_neighbours() -> list[str]:
    """IPv4 peers in the ARP table on Thunderbolt interfaces (not this Mac's own addresses)."""
    devs = set(tb_interfaces())
    if not devs:
        return []
    own = {str(itf.ip) for dev in devs for itf in iface_ipv4(dev)}
    try:
        out = subprocess.run(["/usr/sbin/arp", "-an"], text=True, capture_output=True, timeout=5).stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []
    ips = []
    for line in out.splitlines():
        m = re.search(r"\((\d+\.\d+\.\d+\.\d+)\) at ([0-9a-f:]+|\(incomplete\)) on (\S+)", line)
        if m and m.group(3) in devs and m.group(2) != "(incomplete)" and not m.group(2).startswith("ff:"):
            if m.group(1) not in own and m.group(1) not in ips:
                ips.append(m.group(1))
    return ips


def parse_ndp_neighbours(text: str, ifaces: set[str], own: set[str]) -> list[str]:
    """Scoped fe80 neighbours (`fe80::…%bridge0`) on Thunderbolt interfaces, excluding this Mac.

    `ndp -an` lines look like `fe80::abcd%bridge0  36:8e:…  bridge0  3s  D`. Incomplete rows and
    neighbours on other interfaces (Wi-Fi, awdl) are ignored. `own` is compared case-insensitively
    and includes the zone id."""
    own_l = {a.lower() for a in own}
    ifaces_l = {i.lower() for i in ifaces}
    found: list[str] = []
    for raw in text.splitlines():
        parts = raw.split()
        if len(parts) < 3 or not parts[0].lower().startswith("fe80:"):
            continue
        scoped, mac, netif = parts[0], parts[1], parts[2]
        if mac == "(incomplete)":
            continue
        zone = scoped.partition("%")[2]
        if netif.lower() not in ifaces_l and zone.lower() not in ifaces_l:
            continue
        if scoped.lower() in own_l or scoped in found:
            continue
        found.append(scoped)
    return found


def iface_ipv6_link_locals(dev: str) -> list[str]:
    """This interface's own fe80 addresses, zone included (`fe80::1%bridge0`)."""
    try:
        out = subprocess.run(["/sbin/ifconfig", dev], text=True, capture_output=True, timeout=5).stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []
    return re.findall(r"inet6 (fe80:[0-9a-f:]+%\S+)", out, flags=re.I)


def parse_dns_sd_aaaa(text: str, ifaces: set[str]) -> list[str]:
    """Link-local AAAA answers from `dns-sd -G v6`, compressed and scoped, on `ifaces` only.

    A query bound to the Thunderbolt bridge (`-i bridge0`) still has to be filtered: the line
    carries the zone (`%bridge0` or `%en0`). Wi-Fi answers are dropped."""
    ifaces_l = {i.lower() for i in ifaces}
    found: list[str] = []
    for line in text.splitlines():
        if not re.search(r"\bAdd\b", line):
            continue
        m = re.search(r"(fe80:[0-9a-f:]+)%([A-Za-z0-9]+)", line, re.I)
        if not m or m.group(2).lower() not in ifaces_l:
            continue
        try:
            ip = ipaddress.IPv6Address(m.group(1))
        except ValueError:
            continue
        scoped = f"{ip.compressed}%{m.group(2)}"
        if scoped not in found:
            found.append(scoped)
    return found


def _dns_sd_getaddr(host: str, iface: str, seconds: float) -> str:
    try:
        proc = subprocess.Popen(["/usr/bin/dns-sd", "-i", iface, "-G", "v6", host],
                                text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    except FileNotFoundError:
        return ""
    try:
        time.sleep(seconds)
        proc.terminate()
        out, _ = proc.communicate(timeout=2)
    except Exception:
        proc.kill()
        return ""
    return out or ""


def _mdns_names(hosts: list[str]) -> list[str]:
    """Configured hostnames as `.local` names. Case-only duplicates collapse.
    A failed lookup must not drop a later name of the same machine: macOS keeps
    advertising `Name-6.local` after `Name-2.local` has gone, and both share one base."""
    seen: set[str] = set()
    names: list[str] = []
    for host in hosts:
        if host.startswith("/") or _looks_like_ip(host):
            continue
        name = host if host.lower().endswith(".local") else host + ".local"
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        names.append(name)
    return names


def _tb_aaaa(name: str, devs: list[str], seconds: float) -> list[str]:
    for dev in devs:
        found = parse_dns_sd_aaaa(_dns_sd_getaddr(name, dev, seconds), {dev})
        if found:
            return found
    return []


def tb_hostname_addrs(hosts: list[str], seconds: float = 1.6) -> list[str]:
    """IPv6 link-local of a peer, asked only on a live Thunderbolt interface.

    `dns-sd -i bridge0 -G v6 name.local` returns the address on that cable. The neighbour
    cache is often empty, so NDP alone misses a peer that is plugged in. Every configured
    name is tried: the first one is often a stale `Name-2.local` from an older boot.
    When none of those answer, a Bonjour instance on the Thunderbolt bridge whose base
    name matches (`mbpM5MaxMF (8)` → `mbpM5MaxMF-6.local`) is queried the same way.
    Wi-Fi answers are dropped by `parse_dns_sd_aaaa`."""
    devs = [d for d in tb_interfaces() if iface_ipv4(d) or iface_ipv6_link_locals(d)]
    if not devs:
        return []
    for name in _mdns_names(hosts):
        found = _tb_aaaa(name, devs, seconds)
        if found:
            return found
    wanted = {base_name(h) for h in hosts if not h.startswith("/") and not _looks_like_ip(h)}
    if not wanted:
        return []
    iface = "bridge0" if "bridge0" in devs else devs[0]
    for service in ("_companion-link._tcp", "_airplay._tcp"):
        for inst in bonjour_ssh_hosts(seconds, iface=iface, service=service):
            if base_name(inst) not in wanted:
                continue
            live = resolve_instance(inst, seconds, service=service)
            if not live:
                continue
            if not live.lower().endswith(".local"):
                live += ".local"
            found = _tb_aaaa(live, devs, seconds)
            if found:
                return found
    return []


def _looks_like_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host.split("%", 1)[0])
    except ValueError:
        return False
    return True


def tb_link_locals() -> list[str]:
    """IPv6 link-local peers on a live Thunderbolt port.

    When bridge0 has only 169.254/16 and not the cluster /24, the peer still answers as
    `fe80::…%bridge0` (NDP). ping6 does not accept the `%zone` form; callers SSH this string,
    which OpenSSH does accept."""
    devs = tb_interfaces()
    if not devs:
        return []
    own: set[str] = set()
    for dev in devs:
        own.update(a.lower() for a in iface_ipv6_link_locals(dev))
    try:
        out = subprocess.run(["/usr/sbin/ndp", "-an"], text=True, capture_output=True, timeout=5).stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []
    return parse_ndp_neighbours(out, set(devs), own)


def order_hosts(hosts: list[str]) -> list[tuple[str, str]]:
    """Return (host, transport) in probe order: live Thunderbolt first, then LAN names/IPs;
    TB addresses without a live link go last so a dead bridge costs no ping timeouts up front."""
    active, configured = tb_networks(), cluster_tb_subnet()
    tb, lan, dead = [], [], []
    for h in hosts:
        state = is_tb_host(h, active, configured)
        if state == "active":
            tb.append((h, "Thunderbolt"))
        elif state == "down":
            dead.append((h, "Thunderbolt (link down)"))
        else:
            lan.append((h, "LAN"))
    return tb + lan + dead
