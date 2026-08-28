"""Network read/apply ports."""

from __future__ import annotations

from ipaddress import IPv4Address
from typing import Protocol

from maccluster.domain.models import BridgeInterface, InterfaceCounters


class NetworkReadPort(Protocol):
    def get_bridge(self, name: str) -> BridgeInterface: ...

    def list_interfaces(self) -> tuple[str, ...]: ...

    def get_iface_counters(self, name: str) -> InterfaceCounters | None:
        """Cumulative counters for one interface (netstat Link row), or None."""
        ...

    def get_iface_counters_many(self, names: tuple[str, ...]) -> dict[str, InterfaceCounters]:
        """Batch counters; missing ifaces omitted."""
        ...


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

    def protect_wifi_from_bridge(self, cluster_ip: str, *, dry_run: bool = False) -> None:
        """Strip TB Bridge default-gateway / put Wi-Fi first. Best-effort."""
        ...
