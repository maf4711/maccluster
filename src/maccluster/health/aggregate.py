"""Health aggregation → exit code hints."""

from __future__ import annotations

from maccluster.cli.exit_codes import DEGRADED, ERROR, OK
from maccluster.domain.enums import OverallHealth
from maccluster.domain.models import HealthSnapshot


def exit_code_for_snapshot(snap: HealthSnapshot) -> int:
    if snap.overall == OverallHealth.HEALTHY:
        return OK
    if snap.overall == OverallHealth.DEGRADED:
        return DEGRADED
    if snap.overall == OverallHealth.UNHEALTHY:
        return ERROR
    return OK
