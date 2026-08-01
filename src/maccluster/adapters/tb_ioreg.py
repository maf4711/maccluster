"""ioreg Thunderbolt fallback probe."""

from __future__ import annotations

import re

from maccluster.constants import TIMEOUT_PROFILER
from maccluster.domain.enums import LinkState
from maccluster.domain.models import ThunderboltPort, ThunderboltSnapshot
from maccluster.ports.process import ProcessRunnerPort


def parse_ioreg_tb(text: str) -> ThunderboltSnapshot:
    """Best-effort parse of ioreg TB-related output."""
    ports: list[ThunderboltPort] = []
    # Look for receptacle / port indices and domain UUIDs
    receptacles = re.findall(r'"AppleThunderbolt(Port|USB4)"', text)
    domains = re.findall(
        r'"Domain UUID"\s*=\s*"([0-9A-Fa-f-]{36})"',
        text,
    )
    # Simpler: count IOThunderboltPort entries
    port_blocks = re.split(r"\+-o\s+AppleThunderbolt", text)
    idx = 0
    for block in port_blocks[1:]:
        idx += 1
        domain = None
        dm = re.search(r'"Domain UUID"\s*=\s*"([0-9A-Fa-f-]{36})"', block)
        if dm:
            domain = dm.group(1)
        elif domains:
            domain = domains[min(idx - 1, len(domains) - 1)]
        link_state = LinkState.UNKNOWN
        if re.search(r"No device|not connected", block, re.I):
            link_state = LinkState.UNCONNECTED
        elif re.search(r"Link Status|Device Connected", block, re.I):
            link_state = LinkState.CONNECTED
        ports.append(
            ThunderboltPort(
                receptacle_id=str(idx),
                interface_name=None,
                capable=True,
                thunderbolt_version="USB4/TB",
                link_speed_gbps=None,
                link_state=link_state,
                domain_uuid=domain,
                peer_name=None,
                bus_uid=None,
                status_raw=None,
            )
        )
    if not ports and receptacles:
        for i, _ in enumerate(receptacles, start=1):
            ports.append(
                ThunderboltPort(
                    receptacle_id=str(i),
                    interface_name=None,
                    capable=True,
                    thunderbolt_version="USB4/TB",
                    link_speed_gbps=None,
                    link_state=LinkState.UNKNOWN,
                    domain_uuid=None,
                    peer_name=None,
                )
            )
    return ThunderboltSnapshot(ports=tuple(ports), source="ioreg")


class IoregTB:
    def __init__(self, runner: ProcessRunnerPort) -> None:
        self._runner = runner

    def probe(self) -> ThunderboltSnapshot:
        # Broad dump; parse is best-effort
        result = self._runner.run(
            ["ioreg", "-c", "IOThunderboltPort", "-l", "-w0"],
            timeout=TIMEOUT_PROFILER,
        )
        if result.returncode != 0 and not result.stdout.strip():
            # try alternate class
            result = self._runner.run(
                ["ioreg", "-p", "IOService", "-n", "AppleThunderboltHAL", "-r", "-l"],
                timeout=TIMEOUT_PROFILER,
            )
        return parse_ioreg_tb(result.stdout or "")


class CompositeTB:
    """system_profiler primary, ioreg fallback if empty."""

    def __init__(self, primary: SystemProfilerLike, fallback: IoregLike) -> None:
        self._primary = primary
        self._fallback = fallback

    def probe(self) -> ThunderboltSnapshot:
        try:
            snap = self._primary.probe()
            if snap.ports:
                return snap
        except Exception:
            pass
        return self._fallback.probe()


# Protocol-ish aliases for typing without circular imports
class SystemProfilerLike:
    def probe(self) -> ThunderboltSnapshot:  # pragma: no cover
        raise NotImplementedError


class IoregLike:
    def probe(self) -> ThunderboltSnapshot:  # pragma: no cover
        raise NotImplementedError


class FakeTB:
    def __init__(self, snapshot: ThunderboltSnapshot | None = None) -> None:
        self._snapshot = snapshot or ThunderboltSnapshot(
            ports=(
                ThunderboltPort(
                    receptacle_id="1",
                    interface_name="bridge0",
                    capable=True,
                    thunderbolt_version="USB4",
                    link_speed_gbps=120.0,
                    link_state=LinkState.UNCONNECTED,
                    domain_uuid="AAAAAAAA-AAAA-AAAA-AAAA-AAAAAAAAAAAA",
                ),
                ThunderboltPort(
                    receptacle_id="2",
                    interface_name="bridge0",
                    capable=True,
                    thunderbolt_version="USB4",
                    link_speed_gbps=120.0,
                    link_state=LinkState.UNCONNECTED,
                    domain_uuid="BBBBBBBB-BBBB-BBBB-BBBB-BBBBBBBBBBBB",
                ),
            ),
            source="fake",
            host_model="Mac mini",
        )

    def probe(self) -> ThunderboltSnapshot:
        return self._snapshot
