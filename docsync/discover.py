"""Find SSH-capable Macs on the LAN via Bonjour (dns-sd) and probe peers."""

from __future__ import annotations

import re
import subprocess
import time


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
