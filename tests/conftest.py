"""Shared pytest fixtures."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

# CI / non-macOS friendly
os.environ.setdefault("MACCLUSTER_SKIP_PLATFORM_GUARD", "1")

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def _no_real_arep(monkeypatch):
    """Unit tests never run the installed ``arep`` binary; rdma reads as unavailable."""
    for module in (
        "maccluster.services.sync_transport",
        "maccluster.services.doctor_service",
        "maccluster.services.status_service",
    ):
        monkeypatch.setattr(f"{module}.arep_status_json", lambda *a, **k: None, raising=False)
    # `status` also reads the real ~/Library/Logs sync-last.json; never in unit tests.
    monkeypatch.setattr(
        "maccluster.services.status_service.read_last_run", lambda *a, **k: None, raising=False
    )


@pytest.fixture(autouse=True)
def _isolated_bench_history(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """bench/speedtest append to a throughput history; never the real ~/.local/state.

    The arep history is redirected too, so compare/doctor tests never see a
    real ~/.autoreplikator/bench-history.jsonl on a developer Mac.
    """
    monkeypatch.setenv("MACCLUSTER_BENCH_HISTORY", str(tmp_path / "bench-history.jsonl"))
    monkeypatch.setenv("MACCLUSTER_AREP_HISTORY", str(tmp_path / "arep-bench-history.jsonl"))


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES


@pytest.fixture
def sample_tb_text(fixtures_dir: Path) -> str:
    path = fixtures_dir / "system_profiler" / "sample_m4_mini.txt"
    if path.exists():
        return path.read_text(encoding="utf-8")
    return """Thunderbolt/USB4:

    Thunderbolt/USB4 Bus 0:

      Vendor Name: Apple Inc.
      Device Name: Mac mini
      UID: 0x05AC0001
      Domain UUID: 11111111-1111-1111-1111-111111111111
      Port:
          Status: No device connected
          Speed: Up to 40 Gb/s
          Receptacle: 1
"""


@pytest.fixture
def valid_4_toml(fixtures_dir: Path) -> str:
    return (fixtures_dir / "configs" / "valid_4.toml").read_text(encoding="utf-8")


@pytest.fixture
def tmp_config(tmp_path: Path, valid_4_toml: str) -> Path:
    p = tmp_path / "cluster.toml"
    p.write_text(valid_4_toml, encoding="utf-8")
    return p


@pytest.fixture
def fake_ctx(tmp_config: Path):
    from ipaddress import IPv4Address

    from maccluster.adapters.clock import FakeClock
    from maccluster.adapters.filesystem import FileSystem
    from maccluster.adapters.identity_macos import FakeIdentity
    from maccluster.adapters.iperf3 import FakeBench
    from maccluster.adapters.launchagent import FakeService
    from maccluster.adapters.lock_file import FileLock
    from maccluster.adapters.network_apply import FakeNetworkApply
    from maccluster.adapters.network_read import FakeNetworkRead
    from maccluster.adapters.ping_macos import FakeReachability
    from maccluster.adapters.platform_macos import FakePlatform
    from maccluster.adapters.process import ProcessRunner
    from maccluster.adapters.tb_ioreg import FakeTB
    from maccluster.app_factory import AppContext
    from maccluster.audit.log import NullAudit
    from maccluster.domain.enums import ReachabilityState
    from maccluster.domain.models import BridgeInterface

    return AppContext(
        config_path=tmp_config,
        json_mode=False,
        verbose=False,
        no_color=True,
        clock=FakeClock(),
        fs=FileSystem(),
        runner=ProcessRunner(),
        tb=FakeTB(),
        net_read=FakeNetworkRead(
            bridges={
                "bridge0": BridgeInterface(
                    name="bridge0",
                    exists=True,
                    admin_up=True,
                    addresses=(IPv4Address("10.42.0.1"),),
                )
            }
        ),
        net_apply=FakeNetworkApply(),
        reachability=FakeReachability(
            {
                "10.42.0.2": ReachabilityState.UP,
                "10.42.0.3": ReachabilityState.UP,
                "10.42.0.4": ReachabilityState.DOWN,
            }
        ),
        service=FakeService(),
        bench=FakeBench(),
        lock=FileLock(),
        identity=FakeIdentity(
            hostname="mac-mini-a",
            hw_uuid="00000000-0000-0000-0000-000000000001",
        ),
        platform=FakePlatform(),
        audit=NullAudit(),
    )
