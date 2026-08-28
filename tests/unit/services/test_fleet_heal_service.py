"""heal --fleet: local ensure, then remote maccluster heal over TB SSH."""

from __future__ import annotations

from pathlib import Path

import pytest

from maccluster.cli.exit_codes import DEGRADED, OK, USAGE
from maccluster.constants import LAUNCH_AGENT_LABEL
from maccluster.errors import CliError
from maccluster.ports.process import ProcessResult
from maccluster.services.fleet_heal_service import (
    exit_for_fleet_heal,
    reject_fleet_combo,
    run_fleet_heal,
)


class HealSshRunner:
    def __init__(self, *, missing: bool = False, sudo: bool = False) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.missing = missing
        self.sudo = sudo

    def resolve(self, basename: str) -> str:
        if basename in {"ssh", "launchctl"}:
            return f"/usr/bin/{basename}"
        raise CliError(f"tool not found: {basename}", exit_code=1)

    def run(self, argv, *, timeout: float = 15.0, check: bool = False) -> ProcessResult:
        full = tuple(str(a) for a in argv)
        self.calls.append(full)
        name = Path(full[0]).name
        if name == "launchctl":
            return ProcessResult(argv=full, returncode=0, stdout="", stderr="")
        remote = full[-1] if full else ""
        if "maccluster" in remote:
            if self.missing:
                return ProcessResult(
                    argv=full,
                    returncode=66,
                    stdout="maccluster not on peer — remote-install\n",
                    stderr="",
                )
            if self.sudo:
                return ProcessResult(
                    argv=full,
                    returncode=1,
                    stdout="admin/sudo required to modify network interfaces\n",
                    stderr="",
                )
            return ProcessResult(
                argv=full,
                returncode=0,
                stdout="already configured bridge0 10.42.0.2\n",
                stderr="",
            )
        if "launchctl kickstart" in remote:
            return ProcessResult(argv=full, returncode=0, stdout="", stderr="")
        return ProcessResult(argv=full, returncode=1, stdout="", stderr="unexpected")


def test_reject_fleet_with_loop():
    with pytest.raises(CliError) as ei:
        reject_fleet_combo(fleet=True, loop=True, watchdog=False)
    assert ei.value.exit_code == USAGE


def test_dry_run_does_not_apply_and_does_not_heal_remote(fake_ctx):
    runner = HealSshRunner()
    fake_ctx.runner = runner
    apply_calls_before = list(fake_ctx.net_apply.calls)
    report = run_fleet_heal(fake_ctx, dry_run=True, peer="node-b")
    assert fake_ctx.net_apply.calls == apply_calls_before
    assert all("maccluster heal" not in " ".join(c) for c in runner.calls)
    assert exit_for_fleet_heal(report, dry_run=True) == OK
    assert report.hops
    assert report.hops[0].node_id == "node-b"


def test_missing_remote_cli_is_skipped_degraded(fake_ctx):
    fake_ctx.runner = HealSshRunner(missing=True)
    report = run_fleet_heal(fake_ctx, peer="node-b")
    assert report.hops[0].skipped is True
    assert "remote-install" in report.hops[0].message
    assert exit_for_fleet_heal(report, dry_run=False) == DEGRADED
    assert fake_ctx.clock.slept == [2.0]


def test_sudo_required_on_peer_is_not_ok(fake_ctx):
    fake_ctx.runner = HealSshRunner(sudo=True)
    report = run_fleet_heal(fake_ctx, peer="node-b")
    hop = report.hops[0]
    assert hop.ok is False
    assert hop.skipped is False
    assert "sudo maccluster heal on node-b" in hop.message
    assert exit_for_fleet_heal(report, dry_run=False) == DEGRADED


def test_together_kickstarts_only_maccluster_heal(fake_ctx):
    runner = HealSshRunner()
    fake_ctx.runner = runner
    report = run_fleet_heal(fake_ctx, peer="node-b", together=True)
    assert report.together is True
    joined = " ".join(" ".join(c) for c in runner.calls)
    assert LAUNCH_AGENT_LABEL in joined
    assert "kickstart" in joined
    assert "exo" not in joined.lower()
    assert "com.merados" not in joined
