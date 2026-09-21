"""Real-process exclusion between bootstrap and local maintenance."""

import os
import select
import subprocess

import pytest

from maccluster.adapters.lock_file import FileLock
from maccluster.errors import CliError
from maccluster.services.remote_install_script import REMOTE_INSTALL_SH, _locked_script


def run_script(home, body, *args):
    return subprocess.run(
        ["/bin/bash", "-c", _locked_script(body), "test", *args],
        env={**os.environ, "HOME": str(home)},
        capture_output=True,
        text=True,
        timeout=10,
    )


def test_local_maintenance_blocks_remote_install(tmp_path):
    path = tmp_path / ".config/maccluster/automation.lock"
    with FileLock().acquire(path, timeout=0):
        result = run_script(tmp_path, "echo must-not-run")
    assert result.returncode == 1
    assert "lock in progress" in result.stderr
    assert not result.stdout
    result = run_script(tmp_path, 'printf "%s" "$1"', "space ' quote $literal")
    assert result.returncode == 0, result.stderr
    assert result.stdout == "space ' quote $literal"


def test_remote_install_blocks_local_until_exit(tmp_path):
    path = tmp_path / ".config/maccluster/automation.lock"
    proc = subprocess.Popen(
        ["/bin/bash", "-c", _locked_script("echo ready; read -r finish")],
        env={**os.environ, "HOME": str(tmp_path)},
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert select.select([proc.stdout], [], [], 5)[0], "bootstrap failed to start"
        assert proc.stdout.readline() == "ready\n"
        with pytest.raises(CliError, match="lock in progress"):
            with FileLock().acquire(path, timeout=0):
                pytest.fail("local update overlapped remote installation")
        inode = path.stat().st_ino
        proc.kill()
        proc.wait(timeout=5)
        with FileLock().acquire(path, timeout=0):
            assert path.stat().st_ino == inode
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.communicate(timeout=5)


def test_remote_lock_refuses_symlink(tmp_path):
    path = tmp_path / ".config/maccluster/automation.lock"
    path.parent.mkdir(parents=True)
    target = tmp_path / "preserve"
    target.write_text("original")
    path.symlink_to(target)
    result = run_script(tmp_path, "echo must-not-run")
    assert result.returncode == 1
    assert not result.stdout
    assert target.read_text() == "original"


def test_complete_bootstrap_shell_syntax():
    result = subprocess.run(
        ["/bin/bash", "-n"], input=REMOTE_INSTALL_SH, text=True, capture_output=True, timeout=5
    )
    assert result.returncode == 0, result.stderr
