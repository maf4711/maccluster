"""Init + load roundtrip via CLI main with fakes is hard; use services."""

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
from maccluster.config.load import load_toml_text
from maccluster.services.init_service import init_cluster


def test_init_roundtrip(tmp_path: Path):
    path = tmp_path / "cluster.toml"
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
    init_cluster(ctx, node_count=2)
    cfg = load_toml_text(path.read_text())
    assert cfg.schema_version == 1
    assert 2 <= len(cfg.nodes) <= 4
    assert any("mac-mini-a" in h for n in cfg.nodes for h in n.hostnames) or any(
        n.hw_uuid == "AAAA" for n in cfg.nodes
    )
