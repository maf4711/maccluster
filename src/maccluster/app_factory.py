"""AppContext composition root."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from maccluster.adapters.clock import SystemClock
from maccluster.adapters.filesystem import FileSystem
from maccluster.adapters.identity_macos import MacOSIdentity
from maccluster.adapters.iperf3 import Iperf3Bench
from maccluster.adapters.launchagent import LaunchAgentService
from maccluster.adapters.lock_file import FileLock
from maccluster.adapters.network_apply import NetworkApply
from maccluster.adapters.network_read import NetworkRead
from maccluster.adapters.ping_macos import PingReachability
from maccluster.adapters.platform_macos import MacOSPlatform
from maccluster.adapters.process import ProcessRunner
from maccluster.adapters.tb_ioreg import CompositeTB, IoregTB
from maccluster.adapters.tb_system_profiler import SystemProfilerTB
from maccluster.audit.log import AuditLog, NullAudit
from maccluster.config.paths import resolve_config_path
from maccluster.ports.audit import AuditPort
from maccluster.ports.bench import BenchPort
from maccluster.ports.clock import ClockPort
from maccluster.ports.filesystem import FileSystemPort
from maccluster.ports.host import HostPort
from maccluster.ports.identity import IdentityPort
from maccluster.ports.lock import LockPort
from maccluster.ports.network import NetworkApplyPort, NetworkReadPort
from maccluster.ports.platform import PlatformPort
from maccluster.ports.process import ProcessRunnerPort
from maccluster.ports.reachability import ReachabilityPort
from maccluster.ports.service import ServicePort
from maccluster.ports.thunderbolt import ThunderboltProbePort


@dataclass
class AppContext:
    config_path: Path
    json_mode: bool
    verbose: bool
    no_color: bool
    clock: ClockPort
    fs: FileSystemPort
    runner: ProcessRunnerPort
    tb: ThunderboltProbePort
    net_read: NetworkReadPort
    net_apply: NetworkApplyPort
    reachability: ReachabilityPort
    service: ServicePort
    bench: BenchPort | None
    lock: LockPort
    identity: IdentityPort
    platform: PlatformPort
    audit: AuditPort
    host: HostPort | None = None

    @staticmethod
    def production(
        *,
        config: str | Path | None = None,
        json_mode: bool = False,
        verbose: bool = False,
        no_color: bool = False,
        audit_enabled: bool = False,
    ) -> AppContext:
        runner = ProcessRunner()
        fs = FileSystem()
        config_path = resolve_config_path(config)
        primary = SystemProfilerTB(runner)
        fallback = IoregTB(runner)
        tb = CompositeTB(primary, fallback)
        from maccluster.adapters.host_macos import HostMacOS

        return AppContext(
            config_path=config_path,
            json_mode=json_mode,
            verbose=verbose,
            no_color=no_color,
            clock=SystemClock(),
            fs=fs,
            runner=runner,
            tb=tb,
            net_read=NetworkRead(runner),
            net_apply=NetworkApply(runner),
            reachability=PingReachability(runner),
            service=LaunchAgentService(runner),
            bench=Iperf3Bench(runner),
            lock=FileLock(),
            identity=MacOSIdentity(runner),
            platform=MacOSPlatform(runner),
            audit=AuditLog(enabled=audit_enabled) if audit_enabled else NullAudit(),
            host=HostMacOS(runner),
        )


def build_test_context(**overrides: Any) -> AppContext:
    """Helper for tests — production defaults with overrides."""
    base = AppContext.production()
    for key, value in overrides.items():
        if not hasattr(base, key):
            raise AttributeError(key)
        setattr(base, key, value)
    return base
