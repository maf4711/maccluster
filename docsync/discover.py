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
