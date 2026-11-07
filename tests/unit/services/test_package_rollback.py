"""Recovery uses a retained, verified wheel from the same live installation."""

import json
import zipfile
from pathlib import Path

import pytest

from maccluster.errors import CliError
from maccluster.services import package_update as update


@pytest.fixture(params=["venv", "pipx"])
def recovery(fake_ctx, tmp_path, monkeypatch, request):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    artifacts = []
    for version in ("0.5.0", "0.6.0"):
        path = tmp_path / f"maccluster-{version}-py3-none-any.whl"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr(
                f"maccluster-{version}.dist-info/METADATA",
                f"Name: maccluster\nVersion: {version}\n",
            )
        artifacts.append(update.inspect_wheel(path))
    program = tmp_path / "bin/maccluster"
    state = {"version": None, "digest": None, "fail": False, "restore_fail": False}
    calls = []

    def install(argv, **kwargs):
        artifact = update.inspect_wheel(Path(argv[-1]))
        calls.append(artifact.version)
        if state["fail"] and artifact.version == "0.6.0":
            state.update(version="broken", digest=None)
            raise CliError("package install failed")
        if state["restore_fail"]:
            raise CliError("restore failed")
        state.update(version=artifact.version, digest=artifact.sha256)
        return ""

    monkeypatch.setattr(
        update, "_installation", lambda **k: (request.param, program, ["installer"])
    )
    monkeypatch.setattr(update, "_run", install)
    monkeypatch.setattr(update, "_live_version", lambda *a, **k: state["version"])
    monkeypatch.setattr(update, "_live_digest", lambda *a, **k: state["digest"])
    update.install_wheel(fake_ctx, artifacts[0])
    artifacts[0].path.unlink()  # The original temporary build has gone away.
    return (
        fake_ctx,
        artifacts,
        state,
        calls,
        tmp_path / "Library/Caches/maccluster/package-update.json",
    )


def test_failed_update_restores_old_wheel_and_preserves_receipt(recovery):
    ctx, artifacts, state, calls, receipt = recovery
    previous = receipt.read_bytes()
    state["fail"] = True
    with pytest.raises(CliError, match="previous wheel restored and verified"):
        update.install_wheel(ctx, artifacts[1])
    assert calls == ["0.5.0", "0.6.0", "0.5.0"]
    assert state["digest"] == artifacts[0].sha256
    assert receipt.read_bytes() == previous


def test_failed_restore_is_reported_without_success_receipt(recovery):
    ctx, artifacts, state, calls, receipt = recovery
    previous = receipt.read_bytes()
    state.update(fail=True, restore_fail=True)
    with pytest.raises(CliError, match="rollback failed"):
        update.install_wheel(ctx, artifacts[1])
    assert calls[-2:] == ["0.6.0", "0.5.0"]
    assert receipt.read_bytes() == previous


def test_external_install_is_never_replaced_with_stale_rollback(recovery):
    ctx, artifacts, state, calls, _ = recovery
    state.update(digest="f" * 64, fail=True)
    with pytest.raises(CliError, match="no verified previous wheel"):
        update.install_wheel(ctx, artifacts[1])
    assert calls == ["0.5.0", "0.6.0"]


def test_corrupt_previous_artifact_blocks_update(recovery):
    ctx, artifacts, _, calls, receipt = recovery
    previous = json.loads(receipt.read_text())
    cached = update._wheel_store() / previous["sha256"] / previous["wheel"]
    with zipfile.ZipFile(cached, "a") as archive:
        archive.writestr("changed.txt", "tampered")
    with pytest.raises(CliError, match="previous wheel failed verification"):
        update.install_wheel(ctx, artifacts[1])
    assert calls == ["0.5.0"]


def test_wrong_restored_digest_is_not_reported_as_recovery(recovery, monkeypatch):
    ctx, artifacts, state, _, _ = recovery
    reads = iter([artifacts[0].sha256, "f" * 64])
    monkeypatch.setattr(update, "_live_digest", lambda *a, **k: next(reads))
    state["fail"] = True
    with pytest.raises(CliError, match="restored installation verification failed"):
        update.install_wheel(ctx, artifacts[1])


def test_successful_update_advances_receipt_and_keeps_previous_wheel(recovery):
    ctx, artifacts, _, calls, receipt = recovery
    assert update.install_wheel(ctx, artifacts[1]).changed
    assert json.loads(receipt.read_text())["sha256"] == artifacts[1].sha256
    assert (update._wheel_store() / artifacts[0].sha256 / artifacts[0].path.name).is_file()
    assert calls == ["0.5.0", "0.6.0"]


def test_timeout_does_not_start_competing_rollback(recovery, monkeypatch):
    ctx, artifacts, _, _, receipt = recovery
    previous = receipt.read_bytes()
    operations = []

    def timeout(*args, **kwargs):
        operations.append(kwargs["operation"])
        raise update._PackageTimeout("install timed out")

    monkeypatch.setattr(update, "_run", timeout)
    with pytest.raises(CliError, match="installer may still be active"):
        update.install_wheel(ctx, artifacts[1])
    assert len(operations) == 1
    assert receipt.read_bytes() == previous
