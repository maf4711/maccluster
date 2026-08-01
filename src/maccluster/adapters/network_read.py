"""Read bridge / interface state via ifconfig."""

from __future__ import annotations

import re
from ipaddress import IPv4Address

from maccluster.constants import TIMEOUT_GENERIC
from maccluster.domain.models import BridgeInterface
from maccluster.ports.process import ProcessRunnerPort


def parse_ifconfig_interface(text: str, name: str) -> BridgeInterface:
    """Parse `ifconfig <name>` output."""
    if not text.strip() or "does not exist" in text.lower() or "no such" in text.lower():
        return BridgeInterface(name=name, exists=False, admin_up=False)

    admin_up = bool(re.search(r"\bUP\b", text))
    addresses: list[IPv4Address] = []
    for m in re.finditer(r"inet\s+(\d+\.\d+\.\d+\.\d+)", text):
        try:
            addresses.append(IPv4Address(m.group(1)))
        except ValueError:
            continue
    members: list[str] = []
    mm = re.search(r"member:\s+(.+)", text)
    if mm:
        members = re.findall(r"(en\d+|bridge\d+|awdl\d+)", mm.group(1))
    # macOS bridge shows member lines
    for m in re.finditer(r"member:\s+(\S+)", text):
        members.append(m.group(1))
    return BridgeInterface(
        name=name,
        exists=True,
        admin_up=admin_up,
        addresses=tuple(dict.fromkeys(addresses)),
        members=tuple(dict.fromkeys(members)),
    )


class NetworkRead:
    def __init__(self, runner: ProcessRunnerPort) -> None:
        self._runner = runner

    def get_bridge(self, name: str) -> BridgeInterface:
        result = self._runner.run(["ifconfig", name], timeout=TIMEOUT_GENERIC)
        text = result.stdout or result.stderr
        if result.returncode != 0 and not result.stdout.strip():
            return BridgeInterface(name=name, exists=False, admin_up=False)
        return parse_ifconfig_interface(text, name)

    def list_interfaces(self) -> tuple[str, ...]:
        result = self._runner.run(["ifconfig", "-l"], timeout=TIMEOUT_GENERIC)
        if result.returncode != 0:
            return ()
        return tuple(result.stdout.split())


class FakeNetworkRead:
    def __init__(
        self,
        bridges: dict[str, BridgeInterface] | None = None,
        interfaces: tuple[str, ...] = ("lo0", "en0", "bridge0"),
    ) -> None:
        self.bridges = bridges or {
            "bridge0": BridgeInterface(
                name="bridge0",
                exists=True,
                admin_up=True,
                addresses=(),
            )
        }
        self.interfaces = interfaces

    def get_bridge(self, name: str) -> BridgeInterface:
        return self.bridges.get(
            name,
            BridgeInterface(name=name, exists=False, admin_up=False),
        )

    def list_interfaces(self) -> tuple[str, ...]:
        return self.interfaces
