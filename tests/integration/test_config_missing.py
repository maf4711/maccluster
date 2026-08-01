"""Missing config exit 2."""

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
from maccluster.services.config_service import load_config


def test_missing_config(tmp_path: Path):
    ctx = AppContext(
        config_path=tmp_path / "nope.toml",
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
    with pytest.raises(ConfigError) as ei:
        load_config(ctx)
    assert ei.value.exit_code == 2
    assert "init" in ei.value.message
