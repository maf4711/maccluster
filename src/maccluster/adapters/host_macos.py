"""Collect a HostSnapshot via allowlisted macOS tools."""

from __future__ import annotations

from maccluster.doctor_logic.host_parse import snapshot_from_raw
from maccluster.domain.models import HostSnapshot
from maccluster.errors import CliError
from maccluster.ports.process import ProcessRunnerPort

_TOOL_TIMEOUT_S = 3.0
_SNTP_TIMEOUT_S = 2.5


class HostMacOS:
    def __init__(self, runner: ProcessRunnerPort) -> None:
        self._runner = runner

    def snapshot(self, node_id: str) -> HostSnapshot:
        texts: dict[str, str] = {}
        errors: list[str] = []
        for key, argv in (
            ("vm_stat", ["vm_stat"]),
            ("df", ["df", "-P", "/"]),
            ("uptime", ["uptime"]),
            ("pmset", ["pmset", "-g", "therm"]),
        ):
            try:
                result = self._runner.run(argv, timeout=_TOOL_TIMEOUT_S)
                texts[key] = result.stdout or ""
            except CliError as exc:
                texts[key] = ""
                errors.append(f"{key}: {exc.message}")

        sntp_missing = False
        sntp_text: str | None = None
        try:
            self._runner.resolve("sntp")
        except CliError:
            sntp_missing = True
        else:
            try:
                result = self._runner.run(
                    ["sntp", "-d", "time.apple.com"],
                    timeout=_SNTP_TIMEOUT_S,
                )
                sntp_text = (result.stdout or "") + (result.stderr or "")
            except CliError as exc:
                errors.append(f"sntp: {exc.message}")
                sntp_missing = True

        error = "; ".join(errors) if errors else None
        snap = snapshot_from_raw(
            node_id,
            vm_stat=texts.get("vm_stat", ""),
            df=texts.get("df", ""),
            uptime=texts.get("uptime", ""),
            pmset=texts.get("pmset", ""),
            sntp=sntp_text,
            sntp_missing=sntp_missing,
            error=error,
        )
        if error and snap.ram_used_gb is None and snap.disk_free_gb is None:
            return snap
        if error and snap.ram_used_gb is None:
            return snap
        # Tool missing for optional sntp is not a host error
        if error and all(e.startswith("sntp:") for e in errors):
            return HostSnapshot(
                node_id=snap.node_id,
                ram_used_gb=snap.ram_used_gb,
                ram_free_gb=snap.ram_free_gb,
                load_1m=snap.load_1m,
                disk_free_gb=snap.disk_free_gb,
                cpu_speed_limit_pct=snap.cpu_speed_limit_pct,
                ntp_offset_s=snap.ntp_offset_s,
                error=None,
                ntp_missing=True,
            )
        return snap


class FakeHost:
    def __init__(self, snapshot: HostSnapshot | None = None) -> None:
        self._snapshot = snapshot
        self.calls: list[str] = []

    def snapshot(self, node_id: str) -> HostSnapshot:
        self.calls.append(node_id)
        if self._snapshot is not None:
            s = self._snapshot
            return HostSnapshot(
                node_id=node_id,
                ram_used_gb=s.ram_used_gb,
                ram_free_gb=s.ram_free_gb,
                load_1m=s.load_1m,
                disk_free_gb=s.disk_free_gb,
                cpu_speed_limit_pct=s.cpu_speed_limit_pct,
                ntp_offset_s=s.ntp_offset_s,
                error=s.error,
                ntp_missing=s.ntp_missing,
            )
        return HostSnapshot(
            node_id=node_id,
            ram_used_gb=8.0,
            ram_free_gb=16.0,
            load_1m=0.5,
            disk_free_gb=200.0,
            cpu_speed_limit_pct=None,
            ntp_offset_s=None,
            ntp_missing=True,
        )
