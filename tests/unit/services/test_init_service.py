"""Init service."""

from __future__ import annotations

from pathlib import Path

import pytest

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
from maccluster.errors import ConfigError
from maccluster.services.init_service import init_cluster


def _ctx(path: Path) -> AppContext:
    return AppContext(
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
        identity=FakeIdentity(),
        platform=FakePlatform(),
        audit=NullAudit(),
    )


def test_init_writes(tmp_path: Path):
    path = tmp_path / "cluster.toml"
    ctx = _ctx(path)
    out, source = init_cluster(ctx, from_keychain=False, save_keychain=False)
    assert out.exists()
    assert source == "template"
    text = out.read_text()
    assert "schema_version = 1" in text
    assert "10.42.0.0/24" in text


def test_init_no_overwrite(tmp_path: Path):
    path = tmp_path / "cluster.toml"
    path.write_text("existing")
    ctx = _ctx(path)
    with pytest.raises(ConfigError):
        init_cluster(ctx, force=False, from_keychain=False, save_keychain=False)
