"""Read bridge / interface state via ifconfig + netstat counters."""

from __future__ import annotations

import re
import time
from ipaddress import IPv4Address

from maccluster.constants import TIMEOUT_GENERIC
from maccluster.domain.models import BridgeInterface, InterfaceCounters
from maccluster.health.traffic import parse_netstat_ib
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

    def get_iface_counters(self, name: str) -> InterfaceCounters | None:
        result = self._runner.run(
            ["netstat", "-I", name, "-b"],
            timeout=TIMEOUT_GENERIC,
        )
        if result.returncode != 0 and not result.stdout.strip():
            return None
        parsed = parse_netstat_ib(result.stdout, t_mono=time.monotonic())
        return parsed.get(name)

    def get_iface_counters_many(self, names: tuple[str, ...]) -> dict[str, InterfaceCounters]:
        if not names:
            return {}
        # One netstat -ib is cheaper than N calls and covers bridge members.
        result = self._runner.run(["netstat", "-ib"], timeout=TIMEOUT_GENERIC)
        t = time.monotonic()
        if result.returncode != 0 and not result.stdout.strip():
            # Fallback per-iface
            out: dict[str, InterfaceCounters] = {}
            for n in names:
                c = self.get_iface_counters(n)
                if c is not None:
                    out[n] = c
            return out
        all_ifaces = parse_netstat_ib(result.stdout, t_mono=t)
        return {n: all_ifaces[n] for n in names if n in all_ifaces}


class FakeNetworkRead:
    def __init__(
        self,
        bridges: dict[str, BridgeInterface] | None = None,
        interfaces: tuple[str, ...] = ("lo0", "en0", "bridge0"),
        counters: dict[str, InterfaceCounters] | None = None,
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
        self.counters = counters or {}
        self._counter_tick = 0

    def get_bridge(self, name: str) -> BridgeInterface:
        return self.bridges.get(
            name,
            BridgeInterface(name=name, exists=False, admin_up=False),
        )

    def list_interfaces(self) -> tuple[str, ...]:
        return self.interfaces

    def get_iface_counters(self, name: str) -> InterfaceCounters | None:
        return self.get_iface_counters_many((name,)).get(name)

    def get_iface_counters_many(self, names: tuple[str, ...]) -> dict[str, InterfaceCounters]:
        """Each call advances synthetic counters so rates become available."""
        self._counter_tick += 1
        out: dict[str, InterfaceCounters] = {}
        for n in names:
            base = self.counters.get(n)
            if base is None:
                # default growing traffic
                base = InterfaceCounters(
                    name=n,
                    ipkts=0,
                    ierrs=0,
                    ibytes=0,
                    opkts=0,
                    oerrs=0,
                    obytes=0,
                    coll=0,
                    t_mono=0.0,
                )
            # +1_000_000 bytes/s rx, +500_000 bytes/s tx per tick unit
            tick = self._counter_tick
            out[n] = InterfaceCounters(
                name=n,
                ipkts=base.ipkts + 1000 * tick,
                ierrs=base.ierrs,
                ibytes=base.ibytes + 1_000_000 * tick,
                opkts=base.opkts + 500 * tick,
                oerrs=base.oerrs + (1 if tick > 1 and n == "bridge0" else 0),
                obytes=base.obytes + 500_000 * tick,
                coll=base.coll,
                t_mono=float(tick),
            )
        return out
