"""Remote install plans are inert and transfer flags are honored end to end."""

import shlex
from pathlib import Path

import pytest

from maccluster.errors import CliError
from maccluster.ports.process import ProcessResult
from maccluster.services import remote_install_service as service


class Runner:
    def __init__(self, *, fail_copy=False):
        self.calls = []
        self.fail_copy = fail_copy

    def resolve(self, name):
        return f"/usr/bin/{name}"

    def run(self, argv, **kwargs):
        self.calls.append(argv)
        out = "/tmp/maccluster-install.abc123\n" if "mktemp -d" in argv[-1] else ""
        rc = 1 if self.fail_copy and Path(argv[0]).name == "scp" else 0
        return ProcessResult(tuple(argv), rc, out, "copy failed" if rc else "")


def test_remote_dry_run_has_no_install_preflight_side_effects(fake_ctx, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("dry-run performed a side effect")

    fake_ctx.runner = Runner()
    monkeypatch.setattr(service, "write_cluster_ssh_config", forbidden)
    monkeypatch.setattr(service, "find_or_build_wheel", forbidden)
    monkeypatch.setattr(service, "read_pubkey", forbidden)
    result = service.remote_install(fake_ctx, "node-b", dry_run=True)
    assert result.ok and "dry-run" in result.message
    assert fake_ctx.runner.calls == []


@pytest.mark.parametrize("copy_config", [False, True])
def test_remote_config_option_controls_upload_and_script(
    fake_ctx, tmp_path, monkeypatch, copy_config
):
    runner = Runner()
    fake_ctx.runner = runner
    wheel = tmp_path / "maccluster-0.5.0-py3-none-any.whl"
    wheel.write_bytes(b"wheel-fixture")
    monkeypatch.setattr(service, "read_pubkey", lambda: "ssh-ed25519 fixture\n")
    service.remote_install(
        fake_ctx,
        "node-b",
        copy_config=copy_config,
        wheel_path=wheel,
        setup_ssh_config=False,
        preflight_speedtest=False,
        preserve_config=True,
    )
    uploads = [c for c in runner.calls if Path(c[0]).name == "scp"]
    assert any(c[-2] == str(fake_ctx.config_path) for c in uploads) is copy_config
    install = next(c[-1] for c in runner.calls if c[-1].startswith("/bin/bash "))
    args = shlex.split(install)
    assert (args[3] != "-") is copy_config
    assert args[-1] == "1"
    assert runner.calls[-1][-1].startswith("/bin/rm -rf -- /tmp/maccluster-install.")


def test_remote_transfer_failure_cleans_private_staging(fake_ctx, tmp_path, monkeypatch):
    runner = Runner(fail_copy=True)
    fake_ctx.runner = runner
    wheel = tmp_path / "maccluster-0.5.0-py3-none-any.whl"
    wheel.write_bytes(b"wheel-fixture")
    monkeypatch.setattr(service, "read_pubkey", lambda: "ssh-ed25519 fixture\n")
    with pytest.raises(CliError, match="scp failed"):
        service.remote_install(
            fake_ctx, "node-b", wheel_path=wheel, setup_ssh_config=False, preflight_speedtest=False
        )
    assert runner.calls[-1][-1].startswith("/bin/rm -rf -- /tmp/maccluster-install.")


def test_remote_install_refuses_self_ip(fake_ctx):
    with pytest.raises(CliError, match="this Mac"):
        service.remote_install(fake_ctx, "10.42.0.1", dry_run=True)
