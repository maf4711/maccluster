"""Doctor report aggregation → exit code (A-X2)."""

from __future__ import annotations

from maccluster.cli.exit_codes import DEGRADED, ERROR, OK
from maccluster.domain.enums import CheckSeverity
from maccluster.domain.models import DoctorFinding, DoctorReport

# Cluster-relevant warn checks that should yield exit 3
_CLUSTER_WARN_IDS = frozenset(
    {
        "peers",
        "bridge",
        "self",
        "thunderbolt",
        "config",
        "mesh",
        "heal_heartbeat",
        "exo",
        "host",
        "disk",
        "thermal",
    }
)


def _is_cluster_warn(check_id: str) -> bool:
    if check_id in _CLUSTER_WARN_IDS:
        return True
    return check_id.split(":", 1)[0] in _CLUSTER_WARN_IDS


def severity_rank(sev: CheckSeverity) -> int:
    order = {
        CheckSeverity.OK: 0,
        CheckSeverity.INFO: 1,
        CheckSeverity.SKIPPED: 1,
        CheckSeverity.WARN: 2,
        CheckSeverity.ERROR: 3,
    }
    return order.get(sev, 0)


def build_report(findings: list[DoctorFinding] | tuple[DoctorFinding, ...]) -> DoctorReport:
    worst = CheckSeverity.OK
    for f in findings:
        if severity_rank(f.severity) > severity_rank(worst):
            worst = f.severity
    exit_code = exit_for_findings(findings, worst)
    return DoctorReport(findings=tuple(findings), worst=worst, exit_code=exit_code)


def exit_for_findings(
    findings: list[DoctorFinding] | tuple[DoctorFinding, ...],
    worst: CheckSeverity | None = None,
) -> int:
    if worst is None:
        worst = CheckSeverity.OK
        for f in findings:
            if severity_rank(f.severity) > severity_rank(worst):
                worst = f.severity
    if worst == CheckSeverity.ERROR:
        return ERROR
    # Cluster warn → degraded
    for f in findings:
        if f.severity == CheckSeverity.WARN and _is_cluster_warn(f.check_id):
            return DEGRADED
    return OK
