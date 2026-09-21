import plistlib

import pytest

from maccluster.adapters.launchagent import LaunchAgentService
from maccluster.constants import LAUNCH_AGENT_LABEL
from maccluster.errors import CliError
from maccluster.ports.process import ProcessResult


class Runner:
    def __init__(self, failure=False):
        self.failure = failure
        self.calls = []

    def run(self, argv, **kwargs):
        self.calls.append(argv)
        failed = self.failure and argv[1] in ("bootstrap", "print")
        return ProcessResult(
            tuple(argv), 1 if failed else 0, "state = waiting", "load denied" if failed else ""
        )


def test_failed_launchagent_bootstrap_is_not_success(tmp_path):
    program = tmp_path / "maccluster"
    program.touch()
    with pytest.raises(CliError, match="could not be loaded"):
        LaunchAgentService(Runner(failure=True)).install(
            program=program, config_path=tmp_path / "c.toml", interval_seconds=30
        )


def test_loaded_idle_schedule_is_reported_truthfully(tmp_path):
    program = tmp_path / "maccluster"
    program.touch()
    result = LaunchAgentService(Runner()).install(
        program=program,
        config_path=tmp_path / "c.toml",
        interval_seconds=3600,
        label="com.maccluster.automation",
    )
    assert result.installed and not result.running
    assert result.detail == "loaded"
    with open(result.plist_path, "rb") as fh:
        data = plistlib.load(fh)
    assert data["StartInterval"] == 3600
    assert data["RunAtLoad"] is False
    assert "KeepAlive" not in data
    assert data["ProgramArguments"][-3:] == ["automation", "run", "--saved"]


def test_unknown_label_never_installs_heal_loop(tmp_path):
    program = tmp_path / "maccluster"
    program.touch()
    with pytest.raises(CliError, match="unknown service label"):
        LaunchAgentService(Runner()).install(
            program=program,
            config_path=tmp_path / "c.toml",
            interval_seconds=30,
            label=LAUNCH_AGENT_LABEL + ".typo",
        )


def test_reinstall_unchanged_loaded_service_does_not_restart(tmp_path):
    program = tmp_path / "maccluster"
    program.touch()
    runner = Runner()
    service = LaunchAgentService(runner)
    kwargs = dict(program=program, config_path=tmp_path / "c.toml", interval_seconds=30)
    service.install(**kwargs)
    runner.calls.clear()
    service.install(**kwargs)
    assert [args[1] for args in runner.calls] == ["print"]
