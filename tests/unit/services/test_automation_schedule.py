import json

import pytest

from maccluster.errors import CliError
from maccluster.services import automation_schedule as schedule
from maccluster.services import service_mgmt


def test_schedule_persists_options_and_preserves_other_services(fake_ctx, tmp_path, monkeypatch):
    program = tmp_path / "maccluster"
    program.touch()
    monkeypatch.setattr(service_mgmt, "_resolve_maccluster_program", lambda: program)
    service_mgmt.install_service(fake_ctx)
    schedule.install_automation_service(
        fake_ctx,
        source=str(program),
        peer="node-b",
        interval_seconds=7200,
        no_update=True,
        sync=True,
    )
    saved = schedule.load_automation_settings(fake_ctx)
    assert saved["peer"] == "node-b" and saved["no_update"] and saved["sync"]
    assert fake_ctx.fs.exists(schedule.settings_path(fake_ctx))
    assert service_mgmt.service_status(fake_ctx).installed
    assert schedule.automation_service_status(fake_ctx).installed
    schedule.uninstall_automation_service(fake_ctx)
    assert not schedule.automation_service_status(fake_ctx).installed
    assert service_mgmt.service_status(fake_ctx).installed


@pytest.mark.parametrize("interval", [0, -1, 299])
def test_schedule_refuses_short_interval_without_writes(fake_ctx, interval):
    with pytest.raises(CliError, match="interval"):
        schedule.install_automation_service(fake_ctx, source="wheel", interval_seconds=interval)
    assert not schedule.settings_path(fake_ctx).exists()


@pytest.mark.parametrize("payload", [None, [], {}, {"unknown": True}])
def test_bad_saved_options_fail_closed(fake_ctx, payload):
    schedule.settings_path(fake_ctx).write_text(json.dumps(payload))
    with pytest.raises(CliError):
        schedule.load_automation_settings(fake_ctx)


def test_settings_follow_resolved_config_like_launchagent(fake_ctx, tmp_path):
    target = tmp_path / "dotfiles" / "cluster.toml"
    target.parent.mkdir()
    target.write_text(fake_ctx.config_path.read_text())
    fake_ctx.config_path.unlink()
    fake_ctx.config_path.symlink_to(target)
    assert schedule.settings_path(fake_ctx) == target.with_name("automation.json")


def test_saved_pin_roundtrip_and_legacy_settings(fake_ctx, tmp_path, monkeypatch):
    source = tmp_path / "release.whl"
    source.touch()
    monkeypatch.setattr(service_mgmt, "_resolve_maccluster_program", lambda: source)
    schedule.install_automation_service(fake_ctx, source=str(source), expected_sha256="A" * 64)
    saved = schedule.load_automation_settings(fake_ctx)
    assert saved["expected_sha256"] == "a" * 64
    del saved["expected_sha256"]
    schedule.settings_path(fake_ctx).write_text(json.dumps(saved))
    assert schedule.load_automation_settings(fake_ctx)["expected_sha256"] is None
