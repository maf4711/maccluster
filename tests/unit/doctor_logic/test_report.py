"""Doctor report exit codes."""

from __future__ import annotations

from maccluster.doctor_logic.report import build_report
from maccluster.domain.enums import CheckSeverity
from maccluster.domain.models import DoctorFinding


def test_error_exit_1():
    r = build_report([DoctorFinding("config", CheckSeverity.ERROR, "bad")])
    assert r.exit_code == 1


def test_peer_warn_exit_3():
    r = build_report(
        [
            DoctorFinding("config", CheckSeverity.OK, "ok"),
            DoctorFinding("peers", CheckSeverity.WARN, "down"),
        ]
    )
    assert r.exit_code == 3


def test_iperf_info_exit_0():
    r = build_report(
        [
            DoctorFinding("config", CheckSeverity.OK, "ok"),
            DoctorFinding("iperf3", CheckSeverity.INFO, "missing"),
        ]
    )
    assert r.exit_code == 0


def test_disk_and_peer_host_warn_exit_3():
    r = build_report(
        [
            DoctorFinding("config", CheckSeverity.OK, "ok"),
            DoctorFinding("disk", CheckSeverity.WARN, "low"),
        ]
    )
    assert r.exit_code == 3
    r2 = build_report(
        [
            DoctorFinding("config", CheckSeverity.OK, "ok"),
            DoctorFinding("host:node-b", CheckSeverity.WARN, "unreachable"),
        ]
    )
    assert r2.exit_code == 3


def test_ntp_warn_does_not_degrade():
    r = build_report(
        [
            DoctorFinding("config", CheckSeverity.OK, "ok"),
            DoctorFinding("ntp", CheckSeverity.WARN, "offset"),
            DoctorFinding("ntp", CheckSeverity.SKIPPED, "sntp missing"),
        ]
    )
    assert r.exit_code == 0
