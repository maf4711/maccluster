"""Ping reachability adapter."""

from __future__ import annotations

import re

from maccluster.constants import TIMEOUT_PING
from maccluster.domain.enums import ReachabilityState
from maccluster.ports.process import ProcessRunnerPort
from maccluster.ports.reachability import ReachabilityResult


class PingReachability:
    def __init__(self, runner: ProcessRunnerPort) -> None:
        self._runner = runner

    def ping(self, host: str, *, timeout: float = TIMEOUT_PING) -> ReachabilityResult:
        # macOS ping: -c count -W wait_ms (on some) / -t timeout
        # Use -c 1 -t <seconds>
        t = max(1, int(timeout))
        result = self._runner.run(
            ["ping", "-c", "1", "-t", str(t), host],
            timeout=timeout + 1.0,
        )
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

    def ssh_probe(self, target: str, *, timeout: float = 3.0) -> ReachabilityResult:
        from maccluster.adapters.ssh_probe import ssh_probe

        return ssh_probe(self._runner, target, timeout=timeout)


class FakeReachability:
    def __init__(self, states: dict[str, ReachabilityState] | None = None) -> None:
        self.states = states or {}
        self.default = ReachabilityState.UP

    def ping(self, host: str, *, timeout: float = 2.0) -> ReachabilityResult:
        state = self.states.get(host, self.default)
        return ReachabilityResult(
            target=host,
            state=state,
            rtt_ms=1.0 if state == ReachabilityState.UP else None,
            method="ping",
        )

    def ssh_probe(self, target: str, *, timeout: float = 3.0) -> ReachabilityResult:
        return self.ping(target, timeout=timeout)
