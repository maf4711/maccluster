"""Throughput test to a peer: raw TCP (own tiny sink/source server started over ssh)
and the ssh path rsync actually uses. Time-based, so it is quick on Thunderbolt and on Wi-Fi."""

from __future__ import annotations

import os
import random
import socket
import subprocess
import time
from dataclasses import dataclass, field

from docsync.report import fmt_bytes

CHUNK = 4 * 1024 * 1024

# Runs on the peer under `python3 -` (3.9 compatible). One connection: first it sinks
# whatever the client sends until EOF, then it sources data for `secs` seconds.
SERVER_PY = r'''
import os, socket, sys, time
port, secs = int(sys.argv[1]), float(sys.argv[2])
s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(("", port)); s.listen(1); s.settimeout(25)
sys.stdout.write("READY\n"); sys.stdout.flush()
c, _ = s.accept(); c.settimeout(60)
c.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 8 << 20); c.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 8 << 20)
buf = bytearray(4 << 20); n = 0
while True:
    r = c.recv_into(buf)
    if not r: break
    n += r
data = os.urandom(4 << 20); t0 = time.time()
try:
    while time.time() - t0 < secs: c.sendall(data)
    c.shutdown(socket.SHUT_WR)
    while c.recv(65536): pass
except OSError: pass
c.close(); sys.stdout.write("DONE %d\n" % n)
'''


@dataclass
class Result:
    peer: str
    host: str
    via: str
    ping_ms: float | None = None
    tcp_up: float = 0.0    # bytes/s
    tcp_down: float = 0.0
    ssh_up: float = 0.0
    ssh_down: float = 0.0
    errors: list[str] = field(default_factory=list)

    def render(self) -> str:
        head = f"── {self.peer}  {self.host} via {self.via}   ping {('%.1f ms' % self.ping_ms) if self.ping_ms is not None else '-'}"
        rows = [head]
        if self.tcp_up or self.tcp_down:
            rows.append(f"   TCP raw   ↑ {_rate(self.tcp_up)}   ↓ {_rate(self.tcp_down)}")
        if self.ssh_up or self.ssh_down:
            rows.append(f"   ssh path  ↑ {_rate(self.ssh_up)}   ↓ {_rate(self.ssh_down)}   ← ceiling for rsync")
        for e in self.errors:
            rows.append(f"   ⚠ {e}")
        return "\n".join(rows)


def _rate(bps: float) -> str:
    if bps <= 0:
        return "      -      "
    return f"{bps / 2**20:7,.0f} MB/s ({bps * 8 / 1e9:5.2f} Gb/s)"


def _random_block() -> bytes:
    return os.urandom(CHUNK)


def tcp_test(ssh_cmd: list[str], userhost: str, host: str, secs: float) -> tuple[float, float]:
    """host may be 'name (ip)' — connect to the ip."""
    if host.endswith(")") and " (" in host:
        host = host.rsplit(" (", 1)[1][:-1]
    """Start the sink/source server on the peer over ssh, then push and pull over a raw socket."""
    port = random.randint(47000, 47999)
    proc = subprocess.Popen([*ssh_cmd, userhost, f"python3 - {port} {secs}"], text=True,
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    proc.stdin.write(SERVER_PY)
    proc.stdin.close()
    line = proc.stdout.readline()
    if "READY" not in line:
        err = proc.stderr.read()
        proc.kill()
        raise RuntimeError(f"peer server did not start: {err.strip()[-200:] or line.strip()}")
    try:
        c = socket.create_connection((host, port), timeout=10)
    except OSError as e:
        proc.kill()
        raise RuntimeError(f"tcp connect {host}:{port} failed ({e}) — firewall on the peer?") from e
    c.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 8 << 20)
    c.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 8 << 20)
    c.settimeout(60)
    block = _random_block()
    sent, t0 = 0, time.monotonic()
    while time.monotonic() - t0 < secs:
        c.sendall(block)
        sent += len(block)
    up = sent / (time.monotonic() - t0)
    c.shutdown(socket.SHUT_WR)
    buf = bytearray(CHUNK)
    got, t1, first = 0, time.monotonic(), None
    while True:
        r = c.recv_into(buf)
        if not r:
            break
        if first is None:
            first = time.monotonic()
        got += r
    down = got / max(time.monotonic() - (first or t1), 1e-6)
    c.close()
    proc.wait(timeout=30)
    return up, down


def ssh_test(ssh_cmd: list[str], userhost: str, secs: float) -> tuple[float, float]:
    """Upload: pipe random bytes into `cat >/dev/null`. Download: read /dev/zero through ssh."""
    p = subprocess.Popen([*ssh_cmd, userhost, "cat >/dev/null"], stdin=subprocess.PIPE,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    block = _random_block()
    sent, t0 = 0, time.monotonic()
    try:
        while time.monotonic() - t0 < secs:
            p.stdin.write(block)
            sent += len(block)
        p.stdin.close()
        p.wait(timeout=60)
    except (BrokenPipeError, OSError):
        pass
    up = sent / (time.monotonic() - t0)

    p = subprocess.Popen([*ssh_cmd, userhost, "dd if=/dev/zero bs=4m 2>/dev/null"], stdout=subprocess.PIPE,
                         stderr=subprocess.DEVNULL, bufsize=0)
    got, t1 = 0, time.monotonic()
    while time.monotonic() - t1 < secs:
        chunk = p.stdout.read(CHUNK)
        if not chunk:
            break
        got += len(chunk)
    p.kill()
    down = got / (time.monotonic() - t1)
    return up, down


def run(peer_name: str, host: str, via: str, userhost: str, ssh_cmd: list[str], secs: float,
        ping_ms: float | None, skip_tcp: bool = False, skip_ssh: bool = False) -> Result:
    res = Result(peer=peer_name, host=host, via=via, ping_ms=ping_ms)
    if not skip_tcp:
        try:
            res.tcp_up, res.tcp_down = tcp_test(ssh_cmd, userhost, host, secs)
        except Exception as e:  # noqa: BLE001
            res.errors.append(f"tcp: {e}")
    if not skip_ssh:
        try:
            res.ssh_up, res.ssh_down = ssh_test(ssh_cmd, userhost, secs)
        except Exception as e:  # noqa: BLE001
            res.errors.append(f"ssh: {e}")
    return res


def summary(results: list[Result]) -> str:
    best = max(results, key=lambda r: max(r.tcp_down, r.tcp_up, r.ssh_down, r.ssh_up), default=None)
    if not best:
        return "no results"
    lines = ["══ SUMMARY", f"   {'peer':<10} {'host':<20} {'via':<24} {'ping':>8} {'tcp ↑':>10} {'tcp ↓':>10} {'ssh ↑':>10} {'ssh ↓':>10}"]
    for r in results:
        lines.append(f"   {r.peer:<10} {r.host:<20} {r.via:<24} {('%.1f ms' % r.ping_ms) if r.ping_ms is not None else '-':>8} "
                     f"{r.tcp_up / 2**20:>7,.0f} MB/s {r.tcp_down / 2**20:>7,.0f} MB/s {r.ssh_up / 2**20:>7,.0f} MB/s {r.ssh_down / 2**20:>7,.0f} MB/s")
    lines.append(f"   fastest: {best.peer} via {best.via} — rsync will be capped by the ssh path, "
                 f"{fmt_bytes(int(max(best.ssh_up, best.ssh_down)))}/s, and by disk/iCloud on both ends")
    return "\n".join(lines)
