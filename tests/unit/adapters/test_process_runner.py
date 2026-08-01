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


def test_run_true_like_sw_vers_or_echo_blocked():
    runner = ProcessRunner()
    with pytest.raises(CliError):
        runner.run(["echo", "hi"], timeout=2)
