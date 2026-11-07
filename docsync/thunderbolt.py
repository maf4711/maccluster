"""Thunderbolt link discovery: ports, link state, negotiated speed, attached device, bridge IPs."""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field

from docsync import discover


@dataclass
class TBPort:
    bus: str
    receptacle: str
    status: str          # "No device connected" | device name
    link_status: str     # raw, e.g. 0x100 (no device) / 0x2 (link up)
    speed_gbps: int      # "Up to 40 Gb/s" -> 40 ; 0 if unknown
    device: str = ""     # attached device name (other Mac's name, dock, …)
    device_uid: str = ""

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
        out = subprocess.run(["system_profiler", "-json", "SPThunderboltDataType"],
                             text=True, capture_output=True, timeout=25).stdout
        return json.loads(out).get("SPThunderboltDataType", [])
    except (FileNotFoundError, subprocess.TimeoutExpired, json.JSONDecodeError, ValueError):
        return []


def _profiler_text() -> str:
    try:
        return subprocess.run(["system_profiler", "SPThunderboltDataType"],
                              text=True, capture_output=True, timeout=25).stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ""


def parse_profiler_text(text: str) -> list[TBPort]:
    """Parse the human-readable output; it exposes Port/Status/Speed which the JSON hides."""
    ports: list[TBPort] = []
    bus = ""
    receptacle = status = link = speed = ""
    device = device_uid = ""
    depth_device = False

    def flush():
        nonlocal receptacle, status, link, speed, device, device_uid
        if bus and (receptacle or status):
            ports.append(TBPort(bus, receptacle, status, link, _speed(speed), device, device_uid))
        receptacle = status = link = speed = ""
        device = device_uid = ""

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        indent = len(raw) - len(raw.lstrip())
        if re.match(r"Thunderbolt(/USB4)? Bus \d+:", line):
            flush()
            bus = line.rstrip(":")
            depth_device = False
            continue
        if indent == 6 and line.endswith(":") and not line.startswith("Port"):
            # an attached device block, e.g. "Mac mini:" / "MacBook Pro:" below the bus
            depth_device = True
            device = line.rstrip(":")
            continue
        key, _, val = line.partition(":")
        key, val = key.strip(), val.strip()
        if key == "Port" and not depth_device:
            depth_device = False
        elif key == "Status":
            status = val
        elif key == "Link Status":
            link = val
        elif key == "Speed":
            speed = val
        elif key == "Receptacle":
            receptacle = val
        elif key == "Device Name" and depth_device:
            device = val
        elif key == "UID" and depth_device:
            device_uid = val
    flush()
    return ports


def state() -> TBState:
    st = TBState(ports=parse_profiler_text(_profiler_text()))
    st.interfaces = discover.tb_interfaces()
    for dev in st.interfaces:
        addrs = [str(i.ip) for i in discover.iface_ipv4(dev)]
        if addrs:
            st.active_ifaces[dev] = addrs
    st.neighbours = discover.tb_neighbours()
    return st


def render(st: TBState) -> str:
    lines = [f"thunderbolt: {st.label()}"]
    for p in st.ports:
        dev = f"  ← {p.device}" if p.connected else ""
        lines.append(f"   {p.bus:<24} receptacle {p.receptacle or '?':<2} {p.status:<22} "
                     f"{('%d Gb/s' % p.speed_gbps) if p.speed_gbps else '-':>9}{dev}")
    if st.interfaces:
        act = ", ".join(f"{d} {'/'.join(a)}" for d, a in st.active_ifaces.items()) or "none with IPv4"
        lines.append(f"   interfaces   {', '.join(st.interfaces)}   active: {act}")
    if st.neighbours:
        lines.append(f"   neighbours   {', '.join(st.neighbours)}  (ARP on TB)")
    return "\n".join(lines)
