"""A-004: init --force backs up existing config before replace."""

from __future__ import annotations

from pathlib import Path

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
from maccluster.services.init_service import init_cluster


def test_init_force_writes_bak(tmp_path: Path):
    path = tmp_path / "cluster.toml"
    path.write_text("marker-original-content\n", encoding="utf-8")
    ctx = AppContext(
        config_path=path,
        json_mode=False,
        verbose=False,
        no_color=True,
        clock=FakeClock(),
        fs=FileSystem(),
        runner=ProcessRunner(),
        tb=FakeTB(),
        net_read=FakeNetworkRead(),
        net_apply=FakeNetworkApply(),
        reachability=FakeReachability(),
        service=FakeService(),
        bench=FakeBench(),
        lock=FileLock(),
        identity=FakeIdentity(hostname="mac-mini-a", hw_uuid="AAAA"),
        platform=FakePlatform(),
        audit=NullAudit(),
    )
    out = init_cluster(ctx, force=True)
    assert out.exists()
    assert "schema_version" in out.read_text(encoding="utf-8")
    bak = path.with_suffix(path.suffix + ".bak")
    # FileSystem may use .bak or timestamp-.bak
    backups = list(tmp_path.glob("cluster.toml*.bak")) + list(tmp_path.glob("*.bak"))
    assert backups, "expected backup file next to original"
    assert any("marker-original-content" in p.read_text(encoding="utf-8") for p in backups)
    assert bak.exists() or any(p.name.startswith("cluster.toml") for p in backups)
