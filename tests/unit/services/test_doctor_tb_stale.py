"""doctor WARN `tb_domain_uuids stale` when live UUIDs match none in config but UIDs do."""

from __future__ import annotations

from pathlib import Path

import pytest

from maccluster.adapters.tb_ioreg import FakeTB
from maccluster.domain.enums import CheckSeverity
from maccluster.mapping.tb_identity import parse_system_profiler_json
from maccluster.render.plain import render_doctor
from maccluster.services.doctor_service import run_doctor

STALE_NODE_A = """
[[nodes]]
id = "node-a"
hostnames = ["mac-mini-a.local", "mac-mini-a"]
ip = "10.42.0.1"
hw_uuid = "00000000-0000-0000-0000-000000000001"
tb_domain_uuids = ["676DF3C0-A43A-4D60-8154-6246AF7FBF00"]
{uids}
"""


def _with_self(config_path: Path, uids_line: str) -> None:
    text = config_path.read_text(encoding="utf-8")
    head, _, rest = text.partition('[[nodes]]\nid = "node-a"')
    # drop the original node-a block (up to the next [[nodes]])
    _, sep, tail = rest.partition("[[nodes]]")
    config_path.write_text(
        head + STALE_NODE_A.format(uids=uids_line) + sep + tail, encoding="utf-8"
    )


@pytest.fixture
def live_tb(fixtures_dir: Path) -> FakeTB:
    return FakeTB(
        parse_system_profiler_json(
            (fixtures_dir / "system_profiler" / "node_a_macos27_2026-08-29.json").read_text()
        )
    )


def test_doctor_warns_stale_domain_uuids(fake_ctx, live_tb):
    _with_self(fake_ctx.config_path, 'tb_controller_uids = ["0x05AC51E771159CF0"]')
    fake_ctx.tb = live_tb
    report = run_doctor(fake_ctx)
    by_id = {f.check_id: f for f in report.findings}
    assert by_id["tb_ids"].severity == CheckSeverity.WARN
    assert by_id["tb_ids"].summary == "tb_domain_uuids stale"
    assert "[warn   ] tb_ids: tb_domain_uuids stale" in render_doctor(report)


def test_doctor_does_not_warn_without_uid_confirmation(fake_ctx, live_tb):
    _with_self(fake_ctx.config_path, "")
    fake_ctx.tb = live_tb
    by_id = {f.check_id: f for f in run_doctor(fake_ctx).findings}
    assert by_id["tb_ids"].severity == CheckSeverity.INFO
