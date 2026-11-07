"""Data types of the transfer stage (``sync_transport``): what a peer's plan,
target, ladder choice and outcome look like. Pure dataclasses, no logic — kept
apart so ``sync_transport.py`` stays under the 500-line limit."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from maccluster.domain.models import Node
from maccluster.services.transport_ladder import TransportProbe

if TYPE_CHECKING:
    from maccluster.services.sync_service import FileMeta

__all__ = [
    "NO_TRANSFER",
    "RungResult",
    "TransferOutcome",
    "TransferPlan",
    "TransferTarget",
    "TransportChoice",
]


@dataclass(frozen=True)
class TransportChoice:
    """Rungs to try for one peer, in order; ``detail`` says why some are missing."""

    rungs: tuple[str, ...]
    detail: str = ""
    probe: TransportProbe | None = None


@dataclass(frozen=True)
class TransferPlan:
    """Output of ``plan_transfers`` (+ batch limits) for one peer."""

    to_push: Sequence[str]
    to_pull: Sequence[str]
    push_sizes: Mapping[str, int]
    pull_sizes: Mapping[str, int]
    local_inv: Mapping[str, FileMeta]
    remote_inv: Mapping[str, FileMeta]
    policy: str = "newer"

    @property
    def push_bytes(self) -> int:
        return sum(int(self.push_sizes.get(r, 0) or 0) for r in self.to_push)

    @property
    def pull_bytes(self) -> int:
        return sum(int(self.pull_sizes.get(r, 0) or 0) for r in self.to_pull)

    @property
    def total_bytes(self) -> int:
        return self.push_bytes + self.pull_bytes


@dataclass(frozen=True)
class TransferTarget:
    """Where one peer's bytes go: TB ssh target (+bind) and the Wi-Fi fallback."""

    node: Node
    ssh_target: str  # target the inventory ran on (TB, or .local on the wifi pass)
    bind_ip: str | None  # self TB IP for the tb rung; None on the wifi pass
    wifi_target: str | None  # user@host.local for the wifi rung
    local_home: Path
    remote_home: str

    def ssh_for(self, rung: str) -> tuple[str, str | None]:
        """(ssh_target, bind_ip) for *rung*: wifi is never bound to the bridge."""
        if rung == "wifi":
            return (self.wifi_target or self.ssh_target), None
        return self.ssh_target, self.bind_ip


@dataclass(frozen=True)
class TransferOutcome:
    """What the ladder achieved for one peer (same shape ``sync_home`` reported before)."""

    transport: str  # rung that ran last ("" when none was available)
    push_rc: int
    pull_rc: int
    push_stdout: str = ""
    pull_stdout: str = ""
    push_stderr: str = ""
    pull_stderr: str = ""
    push_bytes_done: int = 0
    pull_bytes_done: int = 0
    downgrades: tuple[str, ...] = ()  # exact log lines, in order
    messages: tuple[str, ...] = ()  # for SyncPeerResult.message


NO_TRANSFER = TransferOutcome(transport="", push_rc=0, pull_rc=0)


@dataclass
class RungResult:
    """Mutable per-rung result; ``reason`` set ⇒ the rung failed."""

    push: tuple[int, str, str, int] = (0, "", "", 0)
    pull: tuple[int, str, str, int] = (0, "", "", 0)
    reason: str | None = None
    moved: bool = False
    push_done: bool = False
    pull_done: bool = False
