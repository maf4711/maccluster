"""Build one verified wheel and update only an isolated Python installation."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from email.parser import BytesParser
from pathlib import Path
from urllib.parse import urlsplit

from maccluster.app_factory import AppContext
from maccluster.errors import CliError

DEFAULT_SOURCE = "https://github.com/maf4711/maccluster/archive/refs/heads/main.zip"


class _PackageTimeout(CliError):
    """The manager timed out; a descendant might still be modifying files."""


@dataclass(frozen=True)
class WheelArtifact:
    path: Path
    version: str
    sha256: str


@dataclass(frozen=True)
class PackageUpdateResult:
    version: str
    sha256: str
    manager: str
    program: str
    changed: bool
    message: str


def validate_timeout(timeout: float) -> float:
    try:
        value = float(timeout)
    except (TypeError, ValueError) as exc:
        raise CliError("timeout must be a positive finite number", exit_code=2) from exc
    if not math.isfinite(value) or value <= 0:
        raise CliError("timeout must be a positive finite number", exit_code=2)
    return value


def validate_source(source: str, *, require_exists: bool = True) -> str:
    """Accept explicit local artifacts/checkouts or credential-free HTTPS sources."""
    if not isinstance(source, str) or not source.strip() or any(c in source for c in "\r\n\x00"):
        raise CliError("source must be a local path or HTTPS URL", exit_code=2)
    source = source.strip()
    if source.startswith("https://"):
        try:
            parts = urlsplit(source)
            _ = parts.port  # Validate malformed ports before running any package manager.
        except ValueError as exc:
            raise CliError("source contains an invalid HTTPS URL", exit_code=2) from exc
        if not parts.hostname or parts.username or parts.password or parts.query or parts.fragment:
            raise CliError(
                "source HTTPS URL must not contain credentials, query or fragment", exit_code=2
            )
        return source
    if "://" in source or source.startswith("-"):
        raise CliError("source must be a local path or HTTPS URL", exit_code=2)
    path = Path(source).expanduser().resolve()
    if require_exists and not path.exists():
        raise CliError(f"source does not exist: {path}", exit_code=2)
    return str(path)


def validate_expected_sha256(source: str, digest: str | None) -> str | None:
    if digest is None:
        return None
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", digest):
        raise CliError("--sha256 must contain exactly 64 hexadecimal characters", exit_code=2)
    if source.startswith("https://") or Path(source).suffix != ".whl":
        raise CliError("--sha256 requires a local wheel supplied with --source", exit_code=2)
    return digest.lower()


def _run(argv: list[str], *, timeout: float, operation: str) -> str:
    """Never inherit interactive stdin; keep package-manager output out of receipts."""
    env = dict(os.environ)
    env.update(PIP_NO_INPUT="1", PIP_DISABLE_PIP_VERSION_CHECK="1")
    try:
        result = subprocess.run(
            argv,
            shell=False,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
            env=env,
        )
    except subprocess.TimeoutExpired as exc:
        raise _PackageTimeout(f"{operation} timed out after {timeout:g}s") from exc
    except OSError as exc:
        raise CliError(
            f"{operation} could not start: {exc.strerror or type(exc).__name__}"
        ) from exc
    if result.returncode:
        raise CliError(f"{operation} failed (exit {result.returncode})")
    return result.stdout.strip()


def inspect_wheel(path: Path) -> WheelArtifact:
    """Validate distribution metadata and hash by streaming, without loading the wheel."""
    path = path.resolve()
    if path.suffix != ".whl" or not path.is_file():
        raise CliError("expected a wheel file")
    try:
        with zipfile.ZipFile(path) as archive:
            metadata_files = [
                info for info in archive.infolist() if info.filename.endswith(".dist-info/METADATA")
            ]
            if len(metadata_files) != 1 or metadata_files[0].file_size > 1024 * 1024:
                raise CliError("wheel must contain exactly one bounded distribution METADATA")
            metadata = BytesParser().parsebytes(archive.read(metadata_files[0]))
            name = re.sub(r"[-_.]+", "-", metadata.get("Name", "")).lower()
            version = metadata.get("Version", "")
            if name != "maccluster" or not re.fullmatch(
                r"[A-Za-z0-9][A-Za-z0-9.!+_-]{0,127}", version
            ):
                raise CliError("wheel metadata must name maccluster and contain a valid version")
        digest = hashlib.sha256()
        with path.open("rb") as wheel:
            for chunk in iter(lambda: wheel.read(1024 * 1024), b""):
                digest.update(chunk)
    except (OSError, zipfile.BadZipFile, KeyError, ValueError) as exc:
        raise CliError(f"cannot inspect wheel: {type(exc).__name__}") from exc
    return WheelArtifact(path, version, digest.hexdigest())


def build_wheel(
    ctx: AppContext,
    source: str = DEFAULT_SOURCE,
    *,
    destination: Path,
    timeout: float = 600,
    expected_sha256: str | None = None,
) -> WheelArtifact:
    """Build from the requested source exactly once; never select a stale cached wheel."""
    source = validate_source(source)
    expected_sha256 = validate_expected_sha256(source, expected_sha256)
    timeout = validate_timeout(timeout)
    destination.mkdir(parents=True, exist_ok=True)
    if any(destination.iterdir()):
        raise CliError("wheel destination must be empty")
    if not source.startswith("https://") and Path(source).suffix == ".whl":
        # Verify the staged copy that will actually be installed. No build hooks
        # or package manager are invoked for a supplied release artifact.
        staged = destination / Path(source).name
        shutil.copyfile(source, staged)
        artifact = inspect_wheel(staged)
        if expected_sha256 is not None and artifact.sha256 != expected_sha256:
            staged.unlink()
            raise CliError("wheel SHA-256 does not match the approved artifact")
        return artifact
    _run(
        [
            sys.executable,
            "-m",
            "pip",
            "wheel",
            "--no-input",
            "--disable-pip-version-check",
            "--no-deps",
            "--wheel-dir",
            str(destination),
            source,
        ],
        timeout=timeout,
        operation="wheel build",
    )
    wheels = list(destination.glob("*.whl"))
    if len(wheels) != 1:
        raise CliError(f"wheel build produced {len(wheels)} wheels; expected exactly one")
    return inspect_wheel(wheels[0])


def _find_pipx() -> str | None:
    found = shutil.which("pipx")
    if found:
        return found
    for root in (Path.home() / ".local/bin", Path("/opt/homebrew/bin"), Path("/usr/local/bin")):
        candidate = root / "pipx"
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def _venv_installation() -> tuple[str, Path, list[str]]:
    return (
        "venv",
        Path(sys.prefix) / "bin/maccluster",
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--no-input",
            "--disable-pip-version-check",
            "--no-deps",
            "--force-reinstall",
        ],
    )


def _installation(*, timeout: float) -> tuple[str, Path, list[str]]:
    in_venv = sys.prefix != sys.base_prefix
    if not in_venv:
        raise CliError(
            "update requires the CLI from its pipx installation or active virtual environment"
        )
    pipx_managed = (Path(sys.prefix) / "pipx_metadata.json").is_file()
    if in_venv and not pipx_managed:
        return _venv_installation()
    pipx = _find_pipx()
    if pipx:
        bin_dir = _run(
            [pipx, "environment", "--value", "PIPX_BIN_DIR"],
            timeout=timeout,
            operation="pipx location",
        )
        directory = Path(bin_dir).expanduser()
        if not bin_dir or not directory.is_absolute():
            raise CliError("pipx did not return an absolute application directory")
        venvs = _run(
            [pipx, "environment", "--value", "PIPX_LOCAL_VENVS"],
            timeout=timeout,
            operation="pipx environment",
        )
        target = Path(venvs).expanduser() / "maccluster"
        if not venvs or target.resolve() != Path(sys.prefix).resolve():
            raise CliError("pipx targets a different installation; use the original PIPX_HOME")
        return (
            "pipx",
            directory / "maccluster",
            [pipx, "install", "--force", "--pip-args=--no-input"],
        )
    if in_venv:
        return _venv_installation()
    raise CliError("update requires an existing pipx installation or an active virtual environment")


def _live_version(program: Path, *, timeout: float) -> str | None:
    try:
        text = _run(
            [str(program), "--version"], timeout=timeout, operation="installed CLI verification"
        )
    except CliError:
        return None
    prefix = "maccluster "
    return text[len(prefix) :] if text.startswith(prefix) and "\n" not in text else None


def _live_digest(program: Path, *, timeout: float) -> str | None:
    """Read pip's wheel provenance from the interpreter owning the CLI."""
    script = (
        "import importlib.metadata,json; "
        "d=json.loads(importlib.metadata.distribution('maccluster').read_text('direct_url.json') or '{}'); "
        "a=d.get('archive_info',{}); "
        "print(a.get('hashes',{}).get('sha256') or a.get('hash','').removeprefix('sha256='))"
    )
    try:
        digest = _run(
            [str(program.resolve().parent / "python"), "-c", script],
            timeout=timeout,
            operation="installed wheel provenance",
        )
    except (OSError, CliError):
        return None
    return digest if isinstance(digest, str) and re.fullmatch(r"[0-9a-f]{64}", digest) else None


def install_wheel(
    ctx: AppContext,
    artifact: WheelArtifact,
    *,
    timeout: float = 600,
) -> PackageUpdateResult:
    """Hash equality is a no-op only if the installed executable still has that version."""
    timeout = validate_timeout(timeout)
    if inspect_wheel(artifact.path) != artifact:
        raise CliError("wheel changed after build verification")
    manager, program, command = _installation(timeout=min(timeout, 30))
    receipt_path = Path.home() / "Library/Caches/maccluster/package-update.json"
    previous = {}
    try:
        previous = json.loads(ctx.fs.read_text(receipt_path))
        if not isinstance(previous, dict):
            previous = {}
    except (OSError, ValueError):
        pass
    identity = {
        "version": artifact.version,
        "sha256": artifact.sha256,
        "manager": manager,
        "program": str(program),
    }
    retained = _retain_wheel(artifact)
    if (
        all(previous.get(k) == v for k, v in identity.items())
        and _live_version(program, timeout=min(timeout, 30)) == artifact.version
        and _live_digest(program, timeout=min(timeout, 30)) == artifact.sha256
    ):
        ctx.fs.write_text_atomic(
            receipt_path,
            json.dumps({**identity, "wheel": retained.path.name}, indent=2) + "\n",
            mode=0o600,
        )
        return PackageUpdateResult(
            **identity, changed=False, message="verified installed wheel is current"
        )
    rollback = _rollback_candidate(previous, manager=manager, program=program, timeout=timeout)
    try:
        _run([*command, str(retained.path)], timeout=timeout, operation=f"{manager} install")
        if _live_version(program, timeout=min(timeout, 30)) != artifact.version:
            raise CliError(f"installed CLI version does not match wheel version {artifact.version}")
    except CliError as exc:
        if isinstance(exc, _PackageTimeout):
            raise CliError(
                f"{exc}; automatic rollback withheld because installer may still be active"
            ) from exc
        if rollback is None:
            raise CliError(f"{exc}; no verified previous wheel available for rollback") from exc
        try:
            if inspect_wheel(rollback.path) != rollback:
                raise CliError("previous wheel changed before rollback")
            _run([*command, str(rollback.path)], timeout=timeout, operation=f"{manager} rollback")
            if (
                _live_version(program, timeout=min(timeout, 30)) != rollback.version
                or _live_digest(program, timeout=min(timeout, 30)) != rollback.sha256
            ):
                raise CliError("restored installation verification failed")
        except CliError as restore_error:
            raise CliError(f"{exc}; rollback failed: {restore_error}") from restore_error
        raise CliError(f"{exc}; previous wheel restored and verified") from exc
    ctx.fs.write_text_atomic(
        receipt_path,
        json.dumps({**identity, "wheel": retained.path.name}, indent=2) + "\n",
        mode=0o600,
    )
    return PackageUpdateResult(
        **identity, changed=True, message="wheel installed and CLI version verified"
    )


def _wheel_store() -> Path:
    return Path.home() / ".local/share/maccluster/update-wheels"


def _retain_wheel(artifact: WheelArtifact) -> WheelArtifact:
    """Keep exact bytes outside temporary build directories for later recovery."""
    directory = _wheel_store() / artifact.sha256
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    target = directory / artifact.path.name
    with tempfile.NamedTemporaryFile(dir=directory, suffix=".whl", delete=False) as stream:
        temporary = Path(stream.name)
    try:
        shutil.copyfile(artifact.path, temporary)
        copied = inspect_wheel(temporary)
        if (copied.version, copied.sha256) != (artifact.version, artifact.sha256):
            raise CliError("wheel changed while retaining recovery artifact")
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return WheelArtifact(target.resolve(), artifact.version, artifact.sha256)


def _rollback_candidate(previous: dict, *, manager: str, program: Path, timeout: float):
    """Never restore a receipt that belongs to another or externally changed install."""
    name, digest = previous.get("wheel"), previous.get("sha256")
    if (
        previous.get("manager") != manager
        or previous.get("program") != str(program)
        or not isinstance(name, str)
        or Path(name).name != name
        or not name.endswith(".whl")
        or not isinstance(digest, str)
        or not re.fullmatch(r"[0-9a-f]{64}", digest)
    ):
        return None
    candidate = _wheel_store() / digest / name
    if not candidate.is_file():
        return None
    artifact = inspect_wheel(candidate)
    if artifact.sha256 != digest or artifact.version != previous.get("version"):
        raise CliError("retained previous wheel failed verification; refusing update")
    if (
        _live_version(program, timeout=min(timeout, 30)) != artifact.version
        or _live_digest(program, timeout=min(timeout, 30)) != artifact.sha256
    ):
        return None
    return artifact
