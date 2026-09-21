"""Package maintenance verifies artifacts and never falls back to system pip."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from maccluster.errors import CliError
from maccluster.services import package_update as update


def wheel(path: Path, *, name="maccluster", version="0.5.0") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "maccluster-0.5.0.dist-info/METADATA",
            f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n",
        )
    return path


def test_inspect_verifies_metadata_and_streams_hash(tmp_path):
    path = wheel(tmp_path / "maccluster-0.5.0-py3-none-any.whl")
    artifact = update.inspect_wheel(path)
    assert artifact.version == "0.5.0"
    assert artifact.sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
    wheel(path, name="other-package")
    with pytest.raises(CliError, match="metadata"):
        update.inspect_wheel(path)


def test_inspect_rejects_multiple_metadata(tmp_path):
    path = wheel(tmp_path / "maccluster.whl")
    with zipfile.ZipFile(path, "a") as archive:
        archive.writestr("other.dist-info/METADATA", "Name: maccluster\nVersion: 0.5.0\n")
    with pytest.raises(CliError, match="exactly one"):
        update.inspect_wheel(path)


def test_build_uses_current_python_one_fresh_verified_wheel(fake_ctx, tmp_path, monkeypatch):
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        wheel(Path(argv[argv.index("--wheel-dir") + 1]) / "maccluster-0.5.0-py3-none-any.whl")
        return ""

    monkeypatch.setattr(update, "_run", run)
    artifact = update.build_wheel(fake_ctx, destination=tmp_path / "build", timeout=17)
    assert artifact.version == "0.5.0"
    argv, kwargs = calls[0]
    assert argv[:4] == [sys.executable, "-m", "pip", "wheel"]
    assert "--no-input" in argv and "--no-deps" in argv
    assert kwargs["timeout"] == 17
    assert argv[-1] == update.DEFAULT_SOURCE
    with pytest.raises(CliError, match="empty"):
        update.build_wheel(fake_ctx, destination=tmp_path / "build")
    assert len(calls) == 1


def test_run_is_noninteractive_and_bounded(monkeypatch):
    seen = {}

    def run(argv, **kwargs):
        seen.update(kwargs)
        return SimpleNamespace(returncode=0, stdout="done\n")

    monkeypatch.setattr(subprocess, "run", run)
    assert update._run(["python", "-V"], timeout=9, operation="test") == "done"
    assert seen["stdin"] is subprocess.DEVNULL
    assert seen["shell"] is False and seen["timeout"] == 9
    assert seen["env"]["PIP_NO_INPUT"] == "1"

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired("secret-token", 9)

    monkeypatch.setattr(subprocess, "run", timeout)
    with pytest.raises(CliError, match="timed out") as error:
        update._run(["python"], timeout=9, operation="test")
    assert "secret-token" not in str(error.value)


def test_no_system_pip_fallback(monkeypatch):
    monkeypatch.setattr(update, "_find_pipx", lambda: None)
    monkeypatch.setattr(sys, "prefix", "/system")
    monkeypatch.setattr(sys, "base_prefix", "/system")
    with pytest.raises(CliError, match="virtual environment"):
        update._installation(timeout=5)
    monkeypatch.setattr(sys, "prefix", "/virtual")
    manager, program, argv = update._installation(timeout=5)
    assert manager == "venv" and program == Path("/virtual/bin/maccluster")
    assert argv[:3] == [sys.executable, "-m", "pip"]
    assert "--force-reinstall" in argv


def test_pipx_uses_its_actual_bin_directory(monkeypatch, tmp_path):
    prefix = tmp_path / "venvs/maccluster"
    prefix.mkdir(parents=True)
    (prefix / "pipx_metadata.json").write_text("{}")
    monkeypatch.setattr(sys, "prefix", str(prefix))
    monkeypatch.setattr(sys, "base_prefix", "/system")
    monkeypatch.setattr(update, "_find_pipx", lambda: "/opt/homebrew/bin/pipx")
    monkeypatch.setattr(
        update,
        "_run",
        lambda argv, **k: "/custom/bin" if argv[-1] == "PIPX_BIN_DIR" else str(prefix.parent),
    )
    manager, program, argv = update._installation(timeout=5)
    assert manager == "pipx" and program == Path("/custom/bin/maccluster")
    assert argv == ["/opt/homebrew/bin/pipx", "install", "--force", "--pip-args=--no-input"]


def test_install_receipt_noop_requires_live_version(fake_ctx, tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    artifact = update.inspect_wheel(wheel(tmp_path / "maccluster.whl"))
    monkeypatch.setattr(update, "_live_digest", lambda *a, **k: artifact.sha256)
    program = tmp_path / "bin/maccluster"
    monkeypatch.setattr(update, "_installation", lambda **k: ("pipx", program, ["pipx", "install"]))
    calls = []
    monkeypatch.setattr(update, "_run", lambda argv, **kw: calls.append(argv))
    monkeypatch.setattr(update, "_live_version", lambda *a, **k: "0.5.0")
    assert update.install_wheel(fake_ctx, artifact).changed
    assert not update.install_wheel(fake_ctx, artifact).changed
    assert len(calls) == 1
    versions = iter(["0.4.0", "0.4.0", "0.5.0"])
    monkeypatch.setattr(update, "_live_version", lambda *a, **k: next(versions))
    assert update.install_wheel(fake_ctx, artifact).changed
    assert len(calls) == 2
    receipt = json.loads((tmp_path / "Library/Caches/maccluster/package-update.json").read_text())
    assert receipt["sha256"] == artifact.sha256


def test_bad_installed_version_does_not_write_receipt(fake_ctx, tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    artifact = update.inspect_wheel(wheel(tmp_path / "maccluster.whl"))
    monkeypatch.setattr(
        update, "_installation", lambda **k: ("venv", tmp_path / "bin/maccluster", ["pip"])
    )
    monkeypatch.setattr(update, "_run", lambda *a, **k: "")
    monkeypatch.setattr(update, "_live_version", lambda *a, **k: "wrong")
    with pytest.raises(CliError, match="does not match"):
        update.install_wheel(fake_ctx, artifact)
    assert not (tmp_path / "Library/Caches/maccluster/package-update.json").exists()


def test_changed_artifact_is_rejected_before_install(fake_ctx, tmp_path, monkeypatch):
    path = wheel(tmp_path / "maccluster.whl")
    artifact = update.inspect_wheel(path)
    wheel(path, version="0.6.0")
    monkeypatch.setattr(update, "_installation", lambda **k: pytest.fail("must not install"))
    with pytest.raises(CliError, match="changed"):
        update.install_wheel(fake_ctx, artifact)


@pytest.mark.parametrize(
    "source",
    [
        "--help",
        "http://example.com/source.zip",
        "https://user:secret@example.com/source.zip",
        "https://example.com/source.zip?token=secret",
        "x\nother",
    ],
)
def test_source_validation_rejects_options_and_secrets(source):
    with pytest.raises(CliError):
        update.validate_source(source)


@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan")])
def test_timeout_must_be_positive_and_finite(timeout):
    with pytest.raises(CliError, match="positive finite"):
        update.validate_timeout(timeout)


def test_same_version_new_wheel_digest_still_installs(fake_ctx, tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    path = wheel(tmp_path / "maccluster.whl")
    original = update.inspect_wheel(path)
    monkeypatch.setattr(update, "_live_digest", lambda *a, **k: original.sha256)
    monkeypatch.setattr(
        update, "_installation", lambda **k: ("venv", tmp_path / "bin/maccluster", ["pip"])
    )
    installs = []
    monkeypatch.setattr(update, "_run", lambda argv, **kw: installs.append(argv))
    monkeypatch.setattr(update, "_live_version", lambda *a, **k: "0.5.0")
    assert update.install_wheel(fake_ctx, original).changed
    with zipfile.ZipFile(path, "a") as archive:
        archive.writestr("maccluster/added.py", "updated code")
    replacement = update.inspect_wheel(path)
    assert replacement.version == original.version and replacement.sha256 != original.sha256
    assert update.install_wheel(fake_ctx, replacement).changed
    assert len(installs) == 2


def test_active_venv_wins_over_unrelated_pipx(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "prefix", str(tmp_path / "venv"))
    monkeypatch.setattr(sys, "base_prefix", "/system")
    monkeypatch.setattr(update, "_find_pipx", lambda: pytest.fail("must update current venv"))
    manager, program, argv = update._installation(timeout=5)
    assert manager == "venv" and program == tmp_path / "venv/bin/maccluster"
    assert "--force-reinstall" in argv


def test_pipx_managed_venv_uses_pipx(monkeypatch, tmp_path):
    prefix = tmp_path / "venvs/maccluster"
    prefix.mkdir(parents=True)
    monkeypatch.setattr(sys, "prefix", str(prefix))
    monkeypatch.setattr(sys, "base_prefix", "/system")
    (prefix / "pipx_metadata.json").write_text("{}")
    monkeypatch.setattr(update, "_find_pipx", lambda: "/opt/homebrew/bin/pipx")
    monkeypatch.setattr(
        update,
        "_run",
        lambda argv, **k: "/apps" if argv[-1] == "PIPX_BIN_DIR" else str(prefix.parent),
    )
    manager, program, _ = update._installation(timeout=5)
    assert manager == "pipx" and program == Path("/apps/maccluster")


def test_external_same_version_replacement_invalidates_noop(fake_ctx, tmp_path, monkeypatch):
    artifact = update.inspect_wheel(wheel(tmp_path / "maccluster.whl"))
    monkeypatch.setattr(
        update, "_installation", lambda **kw: ("venv", tmp_path / "bin/maccluster", ["pip"])
    )
    installs = []
    monkeypatch.setattr(update, "_run", lambda argv, **kw: installs.append(argv))
    monkeypatch.setattr(update, "_live_version", lambda *a, **kw: artifact.version)
    monkeypatch.setattr(update, "_live_digest", lambda *a, **kw: "f" * 64)
    assert update.install_wheel(fake_ctx, artifact).changed
    assert update.install_wheel(fake_ctx, artifact).changed
    assert len(installs) == 2


def test_pinned_wheel_is_staged_unchanged_without_package_manager(fake_ctx, tmp_path, monkeypatch):
    source = wheel(tmp_path / "release/maccluster-0.5.0-py3-none-any.whl")
    expected = update.inspect_wheel(source).sha256
    monkeypatch.setattr(update, "_run", lambda *a, **k: pytest.fail("must not invoke pip"))
    artifact = update.build_wheel(
        fake_ctx, str(source), destination=tmp_path / "staged", expected_sha256=expected.upper()
    )
    assert artifact.sha256 == expected and artifact.path != source
    wheel(source, version="0.6.0")
    assert update.inspect_wheel(artifact.path) == artifact


def test_pinned_wheel_mismatch_removes_staged_artifact(fake_ctx, tmp_path, monkeypatch):
    source = wheel(tmp_path / "maccluster.whl")
    monkeypatch.setattr(update, "_run", lambda *a, **k: pytest.fail("must not invoke pip"))
    with pytest.raises(CliError, match="approved artifact"):
        update.build_wheel(
            fake_ctx, str(source), destination=tmp_path / "staged", expected_sha256="0" * 64
        )
    assert list((tmp_path / "staged").iterdir()) == []
    assert source.exists()


@pytest.mark.parametrize("digest", ["", "f" * 63, "f" * 65, "g" * 64, 123])
def test_invalid_expected_digest_is_option_error(digest):
    with pytest.raises(CliError) as error:
        update.validate_expected_sha256("release.whl", digest)
    assert error.value.exit_code == 2


@pytest.mark.parametrize("source", ["checkout", "source.tar.gz", "https://example.com/release.whl"])
def test_pinned_source_requires_local_wheel(source):
    with pytest.raises(CliError, match="local wheel"):
        update.validate_expected_sha256(source, "a" * 64)
