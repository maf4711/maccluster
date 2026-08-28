"""doctor --host and --host --fleet."""

from __future__ import annotations

import json
from pathlib import Path

from maccluster.adapters.host_macos import FakeHost, HostMacOS
from maccluster.cli.exit_codes import DEGRADED
from maccluster.doctor_logic.host_parse import snapshot_from_raw
from maccluster.domain.models import HostSnapshot
from maccluster.errors import CliError
from maccluster.ports.process import ProcessResult
from maccluster.services.doctor_host import (
    REMOTE_HOST_SNAPSHOT_CMD,
    findings_from_snapshot,
)
from maccluster.services.doctor_service import run_doctor

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"


def _vm_stat_16k() -> str:
    return (FIXTURES / "vm_stat" / "apple_silicon_16k.txt").read_text(encoding="utf-8")


def _df(avail_blocks: int) -> str:
    return (
        "Filesystem     512-blocks      Used Available Capacity  Mounted on\n"
        f"/dev/disk3s1s1  488555536 400000000  {avail_blocks}    50%    /\n"
    )


class RecordingRunner:
    def __init__(self, *, ssh_by_ip: dict[str, ProcessResult] | None = None) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.ssh_by_ip = ssh_by_ip or {}

    def resolve(self, basename: str) -> str:
        if basename in {
            "ssh",
            "vm_stat",
            "df",
            "uptime",
            "pmset",
            "sntp",
            "rdma_ctl",
        }:
            return f"/usr/bin/{basename}"
        raise CliError(f"tool not found: {basename}", exit_code=1)

    def run(self, argv, *, timeout: float = 15.0, check: bool = False) -> ProcessResult:
        full = tuple(str(a) for a in argv)
        self.calls.append(full)
        joined = " ".join(full)
        if full and Path(full[0]).name == "ssh":
            for ip, result in self.ssh_by_ip.items():
                if ip in joined:
                    return ProcessResult(
                        argv=full,
                        returncode=result.returncode,
                        stdout=result.stdout,
                        stderr=result.stderr,
                    )
            return ProcessResult(argv=full, returncode=255, stdout="", stderr="no route")
        return ProcessResult(argv=full, returncode=1, stdout="", stderr="unexpected")


def _ok_raw_json(*, limit: int | None = 100, disk_blocks: int = 41943040) -> str:
    raw = {
        "vm_stat": _vm_stat_16k(),
        "df": _df(disk_blocks),
        "uptime": "load averages: 0.40 0.30 0.20",
        "pmset": "" if limit is None else f"CPU_Speed_Limit = {limit}\n",
        "sntp": None,
        "sntp_missing": True,
    }
    return json.dumps(raw, separators=(",", ":"))


def test_doctor_without_host_never_runs_vm_stat(fake_ctx):
    runner = RecordingRunner()
    fake_ctx.runner = runner
    fake_ctx.host = None
    report = run_doctor(fake_ctx)
    joined = " ".join(" ".join(c) for c in runner.calls)
    assert "vm_stat" not in joined
    assert "pmset" not in joined
    ids = {f.check_id for f in report.findings}
    assert "host" not in ids
    assert "disk" not in ids


def test_thermal_80_and_disk_10gib_warn():
    snap = snapshot_from_raw(
        "node-a",
        vm_stat=_vm_stat_16k(),
        df=_df(20971520),  # 10 GiB
        uptime="load averages: 1.00 1.00 1.00",
        pmset="CPU_Speed_Limit = 80\n",
        sntp_missing=True,
    )
    findings = findings_from_snapshot(snap)
    by_id = {f.check_id: f for f in findings}
    assert by_id["thermal"].severity.value == "warn"
    assert "80" in by_id["thermal"].summary
    assert by_id["disk"].severity.value == "warn"
    assert "10.00" in by_id["disk"].summary


def test_host_macos_uses_allowlisted_argv():
    class MapRunner:
        def __init__(self) -> None:
            self.calls: list[tuple[str, ...]] = []

        def resolve(self, basename: str) -> str:
            return f"/usr/bin/{basename}"

        def run(self, argv, *, timeout: float = 15.0, check: bool = False) -> ProcessResult:
            full = tuple(str(a) for a in argv)
            self.calls.append(full)
            name = Path(full[0]).name if "/" in full[0] else full[0]
            if name == "vm_stat":
                return ProcessResult(argv=full, returncode=0, stdout=_vm_stat_16k(), stderr="")
            if name == "df":
                return ProcessResult(argv=full, returncode=0, stdout=_df(41943040), stderr="")
            if name == "uptime":
                return ProcessResult(
                    argv=full, returncode=0, stdout="load averages: 0.25 0.20 0.10", stderr=""
                )
            if name == "pmset":
                return ProcessResult(
                    argv=full, returncode=0, stdout="CPU_Speed_Limit = 100\n", stderr=""
                )
            if name == "sntp":
                return ProcessResult(
                    argv=full, returncode=0, stdout="offset: (+0.010)\n", stderr=""
                )
            return ProcessResult(argv=full, returncode=1, stdout="", stderr="no")

    runner = MapRunner()
    snap = HostMacOS(runner).snapshot("self")
    names = [Path(c[0]).name for c in runner.calls]
    assert "vm_stat" in names
    assert ("df", "-P", "/") == runner.calls[names.index("df")][1:] or any(
        c[1:3] == ("-P", "/") for c in runner.calls
    )
    assert snap.ram_used_gb == (2000 * 16384) / (1024**3)
    assert snap.cpu_speed_limit_pct == 100
    assert snap.ntp_offset_s == 0.01


def test_fleet_down_peer_warns_and_keeps_other_snapshots(fake_ctx):
    ssh = {
        "10.42.0.2": ProcessResult(argv=("ssh",), returncode=0, stdout=_ok_raw_json(), stderr=""),
        "10.42.0.3": ProcessResult(argv=("ssh",), returncode=0, stdout=_ok_raw_json(), stderr=""),
        "10.42.0.4": ProcessResult(
            argv=("ssh",), returncode=255, stdout="", stderr="Connection timed out"
        ),
    }
    runner = RecordingRunner(ssh_by_ip=ssh)
    fake_ctx.runner = runner
    fake_ctx.host = FakeHost(
        HostSnapshot(
            node_id="node-a",
            ram_used_gb=8.0,
            ram_free_gb=16.0,
            load_1m=0.2,
            disk_free_gb=200.0,
            cpu_speed_limit_pct=None,
            ntp_offset_s=None,
            ntp_missing=True,
        )
    )
    report = run_doctor(fake_ctx, include_host=True, include_fleet=True)
    ids = {f.check_id: f for f in report.findings}
    assert "host" in ids
    assert ids["host:node-d"].severity.value == "warn"
    assert "unreachable" in ids["host:node-d"].summary
    assert "host:node-b" in ids
    assert "disk:node-b" in ids
    assert report.exit_code == DEGRADED
    joined = " ".join(" ".join(c) for c in runner.calls)
    assert "BindAddress=10.42.0.1" in joined
    assert "python3 -c" in joined or "python3" in joined
    assert REMOTE_HOST_SNAPSHOT_CMD.split()[0] == "python3"


def test_fleet_peer_filter_only_one_hop(fake_ctx):
    ssh = {
        "10.42.0.2": ProcessResult(argv=("ssh",), returncode=0, stdout=_ok_raw_json(), stderr=""),
        "10.42.0.3": ProcessResult(argv=("ssh",), returncode=0, stdout=_ok_raw_json(), stderr=""),
    }
    runner = RecordingRunner(ssh_by_ip=ssh)
    fake_ctx.runner = runner
    fake_ctx.host = FakeHost()
    report = run_doctor(fake_ctx, include_host=True, include_fleet=True, peer="node-b")
    by_id = {f.check_id: f for f in report.findings}
    assert "host:node-b" in by_id
    assert "host:node-c" not in by_id
    assert "host:node-d" not in by_id
    hops = [c for c in runner.calls if c and Path(c[0]).name == "ssh"]
    assert len(hops) == 1
    # fake_ctx already has node-d ping-down → peers WARN / exit 3
    assert report.exit_code == DEGRADED
    assert by_id["host:node-b"].severity.value == "info"
