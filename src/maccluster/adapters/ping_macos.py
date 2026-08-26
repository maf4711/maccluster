"""Ping + TCP reachability adapter (macOS)."""

from __future__ import annotations

import re
import socket
import time

from maccluster.constants import TIMEOUT_PING
from maccluster.domain.enums import ReachabilityState
from maccluster.ports.process import ProcessRunnerPort
from maccluster.ports.reachability import ReachabilityResult


class PingReachability:
    def __init__(self, runner: ProcessRunnerPort) -> None:
        self._runner = runner

    def ping(
        self,
        host: str,
        *,
        timeout: float = TIMEOUT_PING,
        source: str | None = None,
    ) -> ReachabilityResult:
        """ICMP ping. Prefer ``source`` (self cluster IP) so traffic uses TB bridge."""
        t = max(1, int(timeout))
        argv = ["ping", "-c", "1", "-t", str(t)]
        if source:
            # macOS: -S source_addr forces outbound interface selection
            argv.extend(["-S", source])
        argv.append(host)
        result = self._runner.run(argv, timeout=timeout + 1.5)
        if result.returncode == 0:
            rtt = None
            m = re.search(r"time[=<]([\d.]+)\s*ms", result.stdout)
            if m:
                rtt = float(m.group(1))
            return ReachabilityResult(
                target=host,
                state=ReachabilityState.UP,
                rtt_ms=rtt,
                method="ping",
            )
        return ReachabilityResult(
            target=host,
            state=ReachabilityState.DOWN,
            method="ping",
            detail=(result.stderr or result.stdout)[:200],
        )

    def tcp_probe(
        self,
        host: str,
        *,
        port: int = 22,
        timeout: float = 1.5,
    ) -> ReachabilityResult:
        """TCP connect probe — works when ICMP is filtered but SSH/service is up."""
        start = time.perf_counter()
        try:
            with socket.create_connection((host, port), timeout=timeout):
                rtt = (time.perf_counter() - start) * 1000.0
            return ReachabilityResult(
                target=f"{host}:{port}",
                state=ReachabilityState.UP,
                rtt_ms=rtt,
                method=f"tcp:{port}",
            )
        except OSError as exc:
            return ReachabilityResult(
                target=f"{host}:{port}",
                state=ReachabilityState.DOWN,
                method=f"tcp:{port}",
                detail=str(exc)[:200],
            )

    def ssh_probe(self, target: str, *, timeout: float = 3.0) -> ReachabilityResult:
        from maccluster.adapters.ssh_probe import ssh_probe

        return ssh_probe(self._runner, target, timeout=timeout)


class FakeReachability:
    def __init__(self, states: dict[str, ReachabilityState] | None = None) -> None:
        self.states = states or {}
        self.default = ReachabilityState.UP
        self.tcp_states: dict[str, ReachabilityState] = {}

    def ping(
        self,
        host: str,
        *,
        timeout: float = 2.0,
        source: str | None = None,
    ) -> ReachabilityResult:
        state = self.states.get(host, self.default)
        return ReachabilityResult(
            target=host,
            state=state,
            rtt_ms=1.0 if state == ReachabilityState.UP else None,
            method="ping",
            detail=f"source={source}" if source else "",
        )

    def tcp_probe(
        self,
        host: str,
        *,
        port: int = 22,
        timeout: float = 1.5,
    ) -> ReachabilityResult:
        state = self.tcp_states.get(host, self.states.get(host, self.default))
        return ReachabilityResult(
            target=f"{host}:{port}",
            state=state,
            rtt_ms=0.5 if state == ReachabilityState.UP else None,
            method=f"tcp:{port}",
        )

    def ssh_probe(self, target: str, *, timeout: float = 3.0) -> ReachabilityResult:
        host = target.split("@")[-1].split(":")[0]
        return self.ping(host, timeout=timeout)
