"""Automation CLI parsing and JSON output remain usable by LaunchAgents."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from maccluster.cli.automation_parser import add_automation_parsers
from maccluster.commands import automation_cmd
from maccluster.errors import CliError
from maccluster.services import service_mgmt


def parser():
    result = argparse.ArgumentParser()
    add_automation_parsers(result.add_subparsers(dest="command"))
    return result


def test_parser_scopes_and_schedule_options():
    args = parser().parse_args(
        ["automation", "install", "--peer", "node-b", "--sync", "--interval", "900"]
    )
    assert args.automation_action == "install" and args.sync
    assert args.peer == "node-b" and args.interval == 900
    with pytest.raises(SystemExit):
        parser().parse_args(["automation", "run", "--peer", "node-b", "--local-only"])
    with pytest.raises(SystemExit):
        parser().parse_args(["automation"])


def test_update_json_is_a_single_machine_readable_document(fake_ctx, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    fake_ctx.json_mode = True
    args = parser().parse_args(["update", "--dry-run"])
    assert automation_cmd.run(fake_ctx, args) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["command"] == "update" and result["data"]["dry_run"]


def test_saved_options_preserve_dry_run(fake_ctx, monkeypatch, capsys):
    saved = {"local_only": True, "no_update": True}
    monkeypatch.setattr(service_mgmt, "load_automation_settings", lambda ctx: saved, raising=False)
    args = parser().parse_args(["automation", "run", "--saved", "--dry-run"])
    fake_ctx.json_mode = True
    assert automation_cmd.run(fake_ctx, args) == 0
    result = json.loads(capsys.readouterr().out)["data"]
    assert result["dry_run"]
    assert [s["name"] for s in result["steps"]] == ["configuration", "heal", "service", "doctor"]


def test_saved_cannot_silently_discard_explicit_options(fake_ctx):
    args = parser().parse_args(["automation", "run", "--saved", "--no-update"])
    with pytest.raises(CliError, match="explicit"):
        automation_cmd.run(fake_ctx, args)


def test_schedule_install_accepts_loaded_idle_service(fake_ctx, monkeypatch, capsys):
    state = SimpleNamespace(installed=True, running=False, detail="loaded")
    monkeypatch.setattr(
        service_mgmt, "install_automation_service", lambda *a, **k: state, raising=False
    )
    fake_ctx.json_mode = True
    args = parser().parse_args(["automation", "install", "--local-only"])
    assert automation_cmd.run(fake_ctx, args) == 0
    json.loads(capsys.readouterr().out)


def test_update_pin_reaches_build_and_blocks_install(fake_ctx, tmp_path, monkeypatch, capsys):
    import zipfile

    from maccluster.services import automation_service

    source = tmp_path / "release.whl"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr(
            "maccluster-0.5.0.dist-info/METADATA", "Name: maccluster\nVersion: 0.5.0\n"
        )
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(
        automation_service, "install_wheel", lambda *a, **k: pytest.fail("must not install")
    )
    fake_ctx.json_mode = True
    args = parser().parse_args(["update", "--source", str(source), "--sha256", "0" * 64])
    assert automation_cmd.run(fake_ctx, args) == 1
    report = json.loads(capsys.readouterr().out)["data"]
    assert report["expected_sha256"] == "0" * 64
    assert "approved artifact" in report["steps"][0]["message"]


def test_saved_rejects_explicit_pin(fake_ctx):
    args = parser().parse_args(["automation", "run", "--saved", "--sha256", "a" * 64])
    with pytest.raises(CliError, match="explicit"):
        automation_cmd.run(fake_ctx, args)
