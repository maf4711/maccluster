"""macOS host identity via scutil / ioreg / platform."""

from __future__ import annotations

import platform
import socket

from maccluster.constants import TIMEOUT_GENERIC
from maccluster.domain.models import HostIdentity
from maccluster.ports.process import ProcessRunnerPort


class MacOSIdentity:
    def __init__(self, runner: ProcessRunnerPort) -> None:
        self._runner = runner

    def get_identity(self) -> HostIdentity:
        hostname = socket.gethostname()
        hostnames = _hostname_variants(hostname)
        # Prefer LocalHostName from scutil
        try:
            r = self._runner.run(["scutil", "--get", "LocalHostName"], timeout=TIMEOUT_GENERIC)
            if r.returncode == 0 and r.stdout.strip():
                local = r.stdout.strip()
                hostnames = tuple(dict.fromkeys([*hostnames, *_hostname_variants(local)]))
                hostname = local
        except Exception:
            pass
        try:
            r = self._runner.run(["scutil", "--get", "ComputerName"], timeout=TIMEOUT_GENERIC)
            if r.returncode == 0 and r.stdout.strip():
                hostnames = tuple(
                    dict.fromkeys([*hostnames, *_hostname_variants(r.stdout.strip())])
                )
        except Exception:
            pass

        hw_uuid = _read_hw_uuid(self._runner)
        model = platform.machine()
        try:
            r = self._runner.run(["sysctl", "-n", "hw.model"], timeout=TIMEOUT_GENERIC)
            if r.returncode == 0 and r.stdout.strip():
                model = r.stdout.strip()
        except Exception:
            pass

        return HostIdentity(
            hostname=hostname,
            hostnames=hostnames,
            hw_uuid=hw_uuid,
            model=model,
            arch=platform.machine(),
        )


def _hostname_variants(name: str) -> tuple[str, ...]:
    name = name.strip()
    if not name:
        return ()
    variants = {name, name.lower()}
    if name.endswith(".local"):
        variants.add(name[: -len(".local")])
        variants.add(name[: -len(".local")].lower())
    else:
        variants.add(f"{name}.local")
        variants.add(f"{name.lower()}.local")
    # strip domain
    if "." in name and not name.endswith(".local"):
        short = name.split(".")[0]
        variants.add(short)
        variants.add(short.lower())
        variants.add(f"{short}.local")
    return tuple(variants)


def _read_hw_uuid(runner: ProcessRunnerPort) -> str:
    try:
        r = runner.run(
            ["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"],
            timeout=TIMEOUT_GENERIC,
        )
        if r.returncode == 0:
            for line in r.stdout.splitlines():
                if "IOPlatformUUID" in line:
                    # "IOPlatformUUID" = "XXXXXXXX-...."
                    parts = line.split("=", 1)
                    if len(parts) == 2:
                        return parts[1].strip().strip('"')
    except Exception:
        pass
    return ""


class FakeIdentity:
    def __init__(
        self,
        hostname: str = "mac-mini-a",
        hw_uuid: str = "00000000-0000-0000-0000-000000000001",
        hostnames: tuple[str, ...] | None = None,
    ) -> None:
        self._id = HostIdentity(
            hostname=hostname,
            hostnames=hostnames or _hostname_variants(hostname),
            hw_uuid=hw_uuid,
            model="Mac16,11",
            arch="arm64",
        )

    def get_identity(self) -> HostIdentity:
        return self._id
