"""Reachability (ping / TCP / SSH) port."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from maccluster.domain.enums import ReachabilityState


@dataclass(frozen=True)
class ReachabilityResult:
    target: str
    state: ReachabilityState
    rtt_ms: float | None = None
    method: str = "ping"
    detail: str = ""


class ReachabilityPort(Protocol):
    def ping(
        self,
        host: str,
        *,
        timeout: float = 2.0,
        source: str | None = None,
    ) -> ReachabilityResult: ...

    def tcp_probe(
        self,
        host: str,
        *,
        port: int = 22,
        timeout: float = 1.5,
    ) -> ReachabilityResult: ...

    def ssh_probe(self, target: str, *, timeout: float = 3.0) -> ReachabilityResult: ...
