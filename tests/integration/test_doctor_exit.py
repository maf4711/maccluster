"""Doctor exit integration."""

from __future__ import annotations

from maccluster.services.doctor_service import run_doctor


def test_doctor_exit(fake_ctx):
    report = run_doctor(fake_ctx)
    assert report.exit_code in (0, 1, 3)
