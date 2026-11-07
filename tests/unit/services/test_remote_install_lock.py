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
        env={**os.environ, "HOME": str(home), "MACCLUSTER_INSTALL_LOCK_WAIT": "1"},
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


def _install_with_peer_status(tmp_path, status_rc: int):
    """Run the real peer install script with a stub `maccluster` whose `status` exits status_rc."""
    import hashlib

    bin_ = tmp_path / ".local/bin"
    bin_.mkdir(parents=True)
    (bin_ / "maccluster").write_text(
        "#!/bin/bash\n"
        'case "$1" in\n'
        '  --version) echo "maccluster 9.9.9";;\n'
        f'  status) echo "overall=degraded 1/2 peers up"; exit {status_rc};;\n'
        "  *) exit 0;;\n"
        "esac\n"
    )
    (bin_ / "pipx").write_text("#!/bin/bash\nexit 0\n")
    for f in bin_.iterdir():
        f.chmod(0o755)
    wheel = tmp_path / "maccluster-9.9.9-py3-none-any.whl"
    wheel.write_bytes(b"stub")
    pub = tmp_path / "cluster.pub"
    pub.write_text("ssh-ed25519 AAAAstub test\n")
    digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
    return subprocess.run(
        ["/bin/bash", "-c", REMOTE_INSTALL_SH, "test", str(wheel), "-", str(pub), digest, "1"],
        env={
            "HOME": str(tmp_path),
            "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
            "MACCLUSTER_INSTALL_LOCK_WAIT": "1",
        },
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_degraded_cluster_does_not_fail_a_successful_install(tmp_path):
    """node-b 2026-11-07: `status` exited 3 (one peer briefly down) and the old script turned a
    successful install into rc=1 without a reason. Cluster health is a warning, not the result."""
    result = _install_with_peer_status(tmp_path, 3)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (
        "warning: cluster not fully healthy after install (doctor rc=0, status rc=3)"
        in result.stdout
    )
    assert "status=0" in result.stdout
