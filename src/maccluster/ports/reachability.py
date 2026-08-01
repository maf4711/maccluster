"""Reachability (ping/SSH) port."""

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
    def ping(self, host: str, *, timeout: float = 2.0) -> ReachabilityResult: ...

    def ssh_probe(self, target: str, *, timeout: float = 3.0) -> ReachabilityResult: ...
