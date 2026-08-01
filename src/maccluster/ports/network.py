"""Network read/apply ports."""

from __future__ import annotations

from ipaddress import IPv4Address
from typing import Protocol

from maccluster.domain.models import BridgeInterface


class NetworkReadPort(Protocol):
    def get_bridge(self, name: str) -> BridgeInterface: ...

    def list_interfaces(self) -> tuple[str, ...]: ...


class NetworkApplyPort(Protocol):
    def ensure_bridge_and_ip(
        self,
        interface: str,
        ip: IPv4Address,
        *,
        prefixlen: int,
        dry_run: bool = False,
    ) -> None:
        """Ensure interface is up and has the given IPv4 address."""
        ...

    def admin_up(self, interface: str, *, dry_run: bool = False) -> None: ...
