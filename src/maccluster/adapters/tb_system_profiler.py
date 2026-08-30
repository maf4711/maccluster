"""Parse system_profiler SPThunderboltDataType output (pure parse + probe)."""

from __future__ import annotations

import re
from collections.abc import Sequence

from maccluster.constants import TIMEOUT_PROFILER
from maccluster.domain.enums import LinkState
from maccluster.domain.models import ThunderboltPort, ThunderboltSnapshot
from maccluster.ports.process import ProcessRunnerPort

_SPEED_RE = re.compile(r"([\d.]+)\s*Gb/s", re.I)

# Bus-level Device Name is this Mac itself, never the peer.
_GENERIC_MAC_NAMES = (
    "mac mini",
    "macbook pro",
    "macbook air",
    "mac pro",
    "mac studio",
)


def parse_system_profiler_tb(text: str) -> ThunderboltSnapshot:
    """Parse SPThunderboltDataType text into a ThunderboltSnapshot."""
    ports: list[ThunderboltPort] = []
    host_model: str | None = None
    current: dict[str, str | None] = {}

    def flush() -> None:
        nonlocal current
        if not current:
            return
        receptacle = current.get("receptacle") or current.get("port") or "?"
        status = (current.get("status") or "").lower()
        if "no device" in status or status == "":
            link_state = LinkState.UNCONNECTED
        elif "connected" in status or current.get("device_name"):
            # Device Name on bus with connected peer
            if "no device" in status:
                link_state = LinkState.UNCONNECTED
            else:
                link_state = (
                    LinkState.CONNECTED
                    if "connected" in status and "no device" not in status
                    else LinkState.UNCONNECTED
                )
        else:
            link_state = LinkState.UNKNOWN

        # Status: No device connected → unconnected
        if "no device" in status:
            link_state = LinkState.UNCONNECTED
        elif status and "connected" in status:
            link_state = LinkState.CONNECTED

        speed = None
        speed_raw = current.get("speed") or ""
        m = _SPEED_RE.search(speed_raw)
        if m:
            try:
                speed = float(m.group(1))
            except ValueError:
                speed = None

        peer = current.get("device_name")
        # Bus-level Device Name is the local host, never the peer.
        if peer and peer.lower() in _GENERIC_MAC_NAMES:
            peer = None

        peer_mode = current.get("peer_mode") or current.get("mode")
        # Prefer concrete model (Mac16,11) over generic "Mac mini" bus label
        concrete = current.get("peer_device") or current.get("nested_device")
        if concrete and link_state == LinkState.CONNECTED:
            peer = concrete
        ports.append(
            ThunderboltPort(
                receptacle_id=str(receptacle),
                interface_name=current.get("interface"),
                capable=True,
                thunderbolt_version=current.get("version") or "USB4/TB",
                link_speed_gbps=speed,
                link_state=link_state,
                domain_uuid=current.get("domain_uuid"),
                peer_name=peer if link_state == LinkState.CONNECTED else None,
                bus_uid=current.get("uid"),
                status_raw=current.get("status"),
                peer_mode=peer_mode if link_state == LinkState.CONNECTED else None,
                peer_domain_uuid=(
                    current.get("peer_domain_uuid") if link_state == LinkState.CONNECTED else None
                ),
                peer_uid=current.get("peer_uid") if link_state == LinkState.CONNECTED else None,
            )
        )
        current = {}

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("Thunderbolt/USB4 Bus"):
            # New bus block ends any open port (and its nested device block).
            if current.get("receptacle") is not None or current.get("status") is not None:
                flush()
            current = {}
        elif line.startswith("Device Name:"):
            val = line.split(":", 1)[1].strip()
            # Nested peer model under an already-open port (e.g. Mac16,11)
            if current.get("status") and current.get("receptacle"):
                if val and val.lower() not in (
                    "mac mini",
                    "macbook pro",
                    "macbook air",
                    "mac pro",
                    "mac studio",
                ):
                    current["peer_device"] = val
                elif val:
                    current["nested_device"] = current.get("nested_device") or val
                continue
            if current.get("receptacle") or current.get("status"):
                flush()
            current["device_name"] = val
            if host_model is None and val:
                host_model = val
        elif line.startswith("UID:"):
            val = line.split(":", 1)[1].strip()
            # After the port's Status line we're inside the attached device's block:
            # its UID is the peer's controller UID (a display exposes one, a Mac
            # does not on macOS 27) and must never overwrite this bus's UID.
            if current.get("status"):
                current["peer_uid"] = val
            else:
                current["uid"] = val
        elif line.startswith("Domain UUID:"):
            val = line.split(":", 1)[1].strip()
            # After the port's Status line we're in the nested attached-device
            # block — its Domain UUID identifies the PEER port, not this bus.
            if current.get("status"):
                current["peer_domain_uuid"] = val
            else:
                current["domain_uuid"] = val
        elif line.startswith("Status:"):
            current["status"] = line.split(":", 1)[1].strip()
        elif line.startswith("Speed:"):
            current["speed"] = line.split(":", 1)[1].strip()
        elif line.startswith("Receptacle:"):
            current["receptacle"] = line.split(":", 1)[1].strip()
        elif line.startswith("Link Status:"):
            current["link_status"] = line.split(":", 1)[1].strip()
        elif line.startswith("Mode:"):
            # Nested peer device mode (Thunderbolt 3/4/5, USB4, …)
            current["peer_mode"] = line.split(":", 1)[1].strip()
        elif line.startswith("Vendor Name:"):
            # Inside an open port this introduces the nested attached-device
            # block (the peer) — keep collecting. Otherwise it opens a bus.
            if current.get("receptacle") is None and current.get("status") is None:
                current = {}
        elif re.match(r"^Port:\s*$", line) or line == "Port:":
            pass

    if current.get("receptacle") is not None or current.get("status") is not None:
        flush()

    # If parser found nothing, try a looser bus-oriented parse
    if not ports:
        ports = list(_loose_parse(text))

    return ThunderboltSnapshot(
        ports=tuple(ports),
        source="system_profiler",
        host_model=host_model,
    )


def _loose_parse(text: str) -> Sequence[ThunderboltPort]:
    ports: list[ThunderboltPort] = []
    blocks = re.split(r"\n\s*Thunderbolt/USB4 Bus", text)
    for block in blocks[1:] if len(blocks) > 1 else []:
        receptacle_m = re.search(r"Receptacle:\s*(\S+)", block)
        status_m = re.search(r"Status:\s*(.+)", block)
        speed_m = re.search(r"Speed:\s*(.+)", block)
        domain_m = re.search(r"Domain UUID:\s*(\S+)", block)
        uid_m = re.search(r"UID:\s*(\S+)", block)
        status = (status_m.group(1).strip() if status_m else "").lower()
        link_state = (
            LinkState.UNCONNECTED
            if "no device" in status
            else LinkState.CONNECTED
            if "connected" in status
            else LinkState.UNKNOWN
        )
        speed = None
        if speed_m:
            sm = _SPEED_RE.search(speed_m.group(1))
            if sm:
                speed = float(sm.group(1))
        ports.append(
            ThunderboltPort(
                receptacle_id=receptacle_m.group(1) if receptacle_m else "?",
                interface_name=None,
                capable=True,
                thunderbolt_version="USB4/TB",
                link_speed_gbps=speed,
                link_state=link_state,
                domain_uuid=domain_m.group(1) if domain_m else None,
                peer_name=None,
                bus_uid=uid_m.group(1) if uid_m else None,
                status_raw=status_m.group(1).strip() if status_m else None,
            )
        )
    return ports


def run_system_profiler_json(runner: ProcessRunnerPort) -> str:
    """Raw ``system_profiler SPThunderboltDataType -json`` text (the structured form
    carries ``switch_uid_key`` per bus); ``CliError`` when the tool yields nothing."""
    result = runner.run(
        ["system_profiler", "SPThunderboltDataType", "-json"],
        timeout=TIMEOUT_PROFILER,
    )
    if result.returncode != 0 and not result.stdout.strip():
        from maccluster.errors import CliError

        raise CliError(
            f"system_profiler -json failed: {result.stderr.strip() or 'no output'}",
            exit_code=1,
        )
    return result.stdout


class SystemProfilerTB:
    def __init__(self, runner: ProcessRunnerPort) -> None:
        self._runner = runner

    def probe(self) -> ThunderboltSnapshot:
        result = self._runner.run(
            ["system_profiler", "SPThunderboltDataType"],
            timeout=TIMEOUT_PROFILER,
        )
        if result.returncode != 0 and not result.stdout.strip():
            from maccluster.errors import CliError

            raise CliError(
                f"system_profiler failed: {result.stderr.strip() or 'no output'}",
                exit_code=1,
            )
        return parse_system_profiler_tb(result.stdout)
