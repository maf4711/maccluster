"""Doctor service."""

from __future__ import annotations

from maccluster.services.doctor_service import run_doctor


def test_doctor_runs(fake_ctx):
    report = run_doctor(fake_ctx)
    ids = {f.check_id for f in report.findings}
    assert "config" in ids
    assert "self" in ids
    assert "peers" in ids
    assert report.exit_code in (0, 1, 3)
