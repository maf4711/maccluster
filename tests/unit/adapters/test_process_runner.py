"""ProcessRunner allowlist and safety."""

from __future__ import annotations

import pytest

from maccluster.adapters.process import ProcessRunner
from maccluster.errors import CliError


def test_reject_non_allowlisted():
    runner = ProcessRunner()
    with pytest.raises(CliError) as ei:
        runner.resolve("curl")
    assert ei.value.exit_code == 1
    assert "allowlisted" in ei.value.message


def test_resolve_ping():
    runner = ProcessRunner()
    path = runner.resolve("ping")
    assert path.endswith("ping")
    assert path.startswith("/")


def test_ditto_and_scp_allowlisted():
    runner = ProcessRunner()
    for name in ("ditto", "scp"):
        try:
            path = runner.resolve(name)
            assert path.endswith(name)
            assert path.startswith("/")
        except CliError as exc:
            assert "not found" in exc.message
            assert "allowlisted" not in exc.message


def test_run_true_like_sw_vers_or_echo_blocked():
    runner = ProcessRunner()
    with pytest.raises(CliError):
        runner.run(["echo", "hi"], timeout=2)


def test_extra_search_paths_include_user_local_bin():
    """pipx and the peer bootstrap install user tools into ~/.local/bin."""
    import os

    from maccluster.constants import EXTRA_SEARCH_PATHS

    assert os.path.expanduser("~/.local/bin") in EXTRA_SEARCH_PATHS


def test_resolve_iperf3_from_extra_dir(tmp_path):
    tool = tmp_path / "iperf3"
    tool.write_text("#!/bin/sh\n")
    tool.chmod(0o755)
    runner = ProcessRunner(search_paths=("/nonexistent",), extra_paths=(str(tmp_path),))
    assert runner.resolve("iperf3") == str(tool)
