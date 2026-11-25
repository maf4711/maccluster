"""Thunderbolt link discovery: ports, link state, negotiated speed, attached device, bridge IPs."""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from docsync import discover


@dataclass
class TBPort:
    bus: str
    receptacle: str
    status: str          # "No device connected" | device name
    link_status: str     # raw, e.g. 0x100 (no device) / 0x2 (link up)
    speed_gbps: int      # "Up to 40 Gb/s" -> 40 ; 0 if unknown
    device: str = ""     # attached device block label ("Mac mini", "Studio Display", …)
    device_uid: str = ""
    device_model: str = ""   # e.g. Mac16,11
    device_domain: str = ""  # peer's Thunderbolt Domain UUID (maps to a maccluster node)

    @property
    def peer_label(self) -> str:
        node = cluster_node_by_domain(self.device_domain)
        base = self.device or "device"
        if node:
            return f"{node} ({base})"
        return f"{base} {self.device_model}".strip()

    @property
    def connected(self) -> bool:
        return self.status.lower() != "no device connected" and self.status != ""


@dataclass
class TBState:
    ports: list[TBPort] = field(default_factory=list)
    interfaces: list[str] = field(default_factory=list)   # en1, en6, bridge0 …
    active_ifaces: dict[str, list[str]] = field(default_factory=dict)  # iface -> IPv4 addrs
    neighbours: list[str] = field(default_factory=list)

    @property
    def link_up(self) -> bool:
        return any(p.connected for p in self.ports) and bool(self.active_ifaces)

    @property
    def best_speed(self) -> int:
        return max((p.speed_gbps for p in self.ports if p.connected), default=0)

    def label(self) -> str:
        if not self.ports and not self.interfaces:
            return "no Thunderbolt hardware found"
        if self.link_up:
            return f"Thunderbolt {self.best_speed} Gb/s"
        if any(p.connected for p in self.ports):
            return "Thunderbolt cable attached, bridge has no IP"
        return "no Thunderbolt link"


def _speed(text: str) -> int:
    m = re.search(r"(\d+)\s*Gb/s", text or "")
    return int(m.group(1)) if m else 0


def _profiler_json() -> list[dict]:
    try:
        out = subprocess.run(["/usr/sbin/system_profiler", "-json", "SPThunderboltDataType"],
                             text=True, capture_output=True, timeout=25).stdout
        return json.loads(out).get("SPThunderboltDataType", [])
    except (FileNotFoundError, subprocess.TimeoutExpired, json.JSONDecodeError, ValueError):
        return []


def _profiler_text() -> str:
    try:
        return subprocess.run(["/usr/sbin/system_profiler", "SPThunderboltDataType"],
                              text=True, capture_output=True, timeout=25).stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ""


def parse_profiler_text(text: str) -> list[TBPort]:
    """Parse the human-readable output; it exposes Port/Status/Speed which the JSON hides.
    Layout:  'Thunderbolt/USB4 Bus 0:' → 'Port:' (Status/Link Status/Speed/Receptacle)
             → optional attached device block '        Mac mini:' with its own 'Device Name'/'Domain UUID'."""
    ports: list[TBPort] = []
    bus = ""
    cur: dict = {}
    in_device = False

    def flush():
        nonlocal cur, in_device
        if bus and cur:
            ports.append(TBPort(bus, cur.get("receptacle", ""), cur.get("status", ""), cur.get("link", ""),
                                _speed(cur.get("speed", "")), cur.get("device", ""), cur.get("device_uid", ""),
                                cur.get("device_model", ""), cur.get("device_domain", "")))
        cur, in_device = {}, False

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        indent = len(raw) - len(raw.lstrip())
        if re.match(r"Thunderbolt(/USB4)? Bus \d+:", line):
            flush()
            bus = line.rstrip(":")
            continue
        if line == "Port:":
            flush()
            cur = {"status": ""}
            continue
        if line.endswith(":") and indent >= 8 and cur and not in_device and line not in ("Services:",):
            in_device = True
            cur["device"] = line.rstrip(":")
            continue
        key, _, val = line.partition(":")
        key, val = key.strip(), val.strip()
        if not in_device:
            if key == "Status":
                cur["status"] = val
            elif key == "Link Status":
                cur["link"] = val
            elif key == "Speed":
                cur["speed"] = val
            elif key == "Receptacle":
                cur["receptacle"] = val
        else:
            if key == "Device Name" and "device_model" not in cur:
                cur["device_model"] = val
            elif key == "UID" and "device_uid" not in cur:
                cur["device_uid"] = val
            elif key == "Domain UUID" and "device_domain" not in cur:
                cur["device_domain"] = val
    flush()
    return ports


def cluster_node_by_domain(domain_uuid: str) -> str:
    """maccluster's cluster.toml lists each node's tb_domain_uuids — map a peer's domain to its node id."""
    f = Path("~/.config/maccluster/cluster.toml").expanduser()
    if not domain_uuid or not f.exists():
        return ""
    node = ""
    for line in f.read_text().splitlines():
        m = re.match(r'\s*id\s*=\s*"([^"]+)"', line)
        if m:
            node = m.group(1)
        if domain_uuid.lower() in line.lower():
            return node
    return ""


def state() -> TBState:
    st = TBState(ports=parse_profiler_text(_profiler_text()))
    st.interfaces = discover.tb_interfaces()
    for dev in st.interfaces:
        addrs = [str(i.ip) for i in discover.iface_ipv4(dev)]
        if addrs:
            st.active_ifaces[dev] = addrs
    st.neighbours = discover.tb_link_locals() + discover.tb_neighbours()
    return st


def render(st: TBState) -> str:
    lines = [f"thunderbolt: {st.label()}"]
    for p in st.ports:
        dev = f"  ← {p.peer_label}" if p.connected else ""
        lines.append(f"   {p.bus:<24} receptacle {p.receptacle or '?':<2} {p.status:<22} "
                     f"{('%d Gb/s' % p.speed_gbps) if p.speed_gbps else '-':>9}{dev}")
    if st.interfaces:
        act = ", ".join(f"{d} {'/'.join(a)}" for d, a in st.active_ifaces.items()) or "none with IPv4"
        lines.append(f"   interfaces   {', '.join(st.interfaces)}   active: {act}")
    if st.neighbours:
        lines.append(f"   neighbours   {', '.join(st.neighbours)}  (NDP/ARP on TB)")
    return "\n".join(lines)
