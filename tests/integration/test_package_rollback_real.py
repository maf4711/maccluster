"""Exercise real pip replacement and recovery in an isolated temporary venv."""

import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from maccluster.errors import CliError
from maccluster.services import package_update as update


def release(directory, version, *, broken=False):
    path = directory / f"maccluster-{version}-py3-none-any.whl"
    dist = f"maccluster-{version}.dist-info"
    files = {
        "maccluster/__init__.py": "",
        "maccluster/cli.py": f"def main():\n    print('maccluster {'broken' if broken else version}')\n",
        f"{dist}/METADATA": f"Metadata-Version: 2.1\nName: maccluster\nVersion: {version}\n",
        f"{dist}/WHEEL": "Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
        f"{dist}/entry_points.txt": "[console_scripts]\nmaccluster = maccluster.cli:main\n",
    }
    files[f"{dist}/RECORD"] = "".join(f"{name},,\n" for name in [*files, f"{dist}/RECORD"])
    with zipfile.ZipFile(path, "w") as archive:
        for name, contents in files.items():
            archive.writestr(name, contents)
    return update.inspect_wheel(path)


def test_real_install_version_failure_restores_previous_release(fake_ctx, tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.delenv("PYTHONPATH", raising=False)
    monkeypatch.setenv("PIP_NO_INDEX", "1")
    monkeypatch.setenv("PIP_NO_CACHE_DIR", "1")
    env = tmp_path / "venv"
    subprocess.run(
        [sys.executable, "-m", "venv", str(env)], check=True, capture_output=True, timeout=60
    )
    program = env / "bin/maccluster"
    command = [str(env / "bin/python"), "-m", "pip", "install", "--no-deps", "--force-reinstall"]
    monkeypatch.setattr(update, "_installation", lambda **k: ("venv", program, command))
    good = release(tmp_path, "0.5.0")
    bad = release(tmp_path, "0.6.0", broken=True)
    update.install_wheel(fake_ctx, good, timeout=30)
    good.path.unlink()
    receipt = (tmp_path / "Library/Caches/maccluster/package-update.json").read_bytes()
    with pytest.raises(CliError, match="previous wheel restored and verified"):
        update.install_wheel(fake_ctx, bad, timeout=30)
    assert update._live_version(program, timeout=5) == good.version
    assert update._live_digest(program, timeout=5) == good.sha256
    assert (tmp_path / "Library/Caches/maccluster/package-update.json").read_bytes() == receipt
