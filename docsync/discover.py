"""Find SSH-capable Macs on the LAN via Bonjour (dns-sd) and probe peers."""

from __future__ import annotations

import ipaddress
import re
import subprocess
import time
from pathlib import Path


def bonjour_ssh_hosts(seconds: float = 3.0) -> list[str]:
    """Browse _ssh._tcp for a few seconds, return instance names (e.g. 'CM-KWFVR7JGW3')."""
    try:
        proc = subprocess.Popen(["dns-sd", "-B", "_ssh._tcp", "local"], text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
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
        m = re.search(r"_ssh\._tcp\.\s+(.+?)\s*$", line)
        if m and " Add " in line:
            name = m.group(1).strip()
            if name not in found:
                found.append(name)
    return found


def to_local_hostname(instance: str) -> str:
    """'CM-KWFVR7JGW3 (720)' -> 'CM-KWFVR7JGW3.local'."""
    base = re.sub(r"\s*\(\d+\)\s*$", "", instance).strip()
    return f"{base}.local"


def resolve_instance(instance: str, seconds: float = 3.0) -> str | None:
    """dns-sd -L: Bonjour instance -> the host name it can be reached at ('CM-X-3000.local')."""
    try:
        proc = subprocess.Popen(["dns-sd", "-L", instance, "_ssh._tcp", "local"], text=True,
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
        res = subprocess.run(["ping", "-c", "1", "-W", str(timeout_ms), host],
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
        out = subprocess.run(["networksetup", "-listallhardwareports"], text=True, capture_output=True, timeout=5).stdout
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
        out = subprocess.run(["ifconfig", dev], text=True, capture_output=True, timeout=5).stdout
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
    """IPs seen in the ARP table on Thunderbolt interfaces (peers that are linked but not configured)."""
    devs = set(tb_interfaces())
    if not devs:
        return []
    try:
        out = subprocess.run(["arp", "-an"], text=True, capture_output=True, timeout=5).stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []
    ips = []
    for line in out.splitlines():
        m = re.search(r"\((\d+\.\d+\.\d+\.\d+)\) at ([0-9a-f:]+|\(incomplete\)) on (\S+)", line)
        if m and m.group(3) in devs and m.group(2) != "(incomplete)" and not m.group(2).startswith("ff:"):
            ips.append(m.group(1))
    return ips


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
