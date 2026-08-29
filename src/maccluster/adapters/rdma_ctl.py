"""Read-only macOS RDMA status via `rdma_ctl status` (never enable)."""

from __future__ import annotations

from maccluster.constants import TIMEOUT_GENERIC
from maccluster.doctor_logic.host_parse import parse_rdma_enabled
from maccluster.domain.models import RdmaStatus
from maccluster.errors import CliError
from maccluster.ports.process import ProcessRunnerPort


def probe_rdma(runner: ProcessRunnerPort) -> RdmaStatus:
    """Probe OS RDMA state. Safe no-op when tool missing (pre-26.2 / non-TB5)."""
    try:
        abs_bin = runner.resolve("rdma_ctl")
    except CliError:
        return RdmaStatus(
            tool_available=False,
            enabled=None,
            raw="",
            detail="rdma_ctl not found (macOS 26.2+ / TB5; enable is Recovery-OS only)",
        )
    try:
        result = runner.run([abs_bin, "status"], timeout=TIMEOUT_GENERIC)
    except Exception as exc:
        return RdmaStatus(
            tool_available=True,
            enabled=None,
            raw="",
            detail=f"rdma_ctl status failed: {exc}",
        )
    text = (result.stdout or result.stderr or "").strip()
    enabled = parse_rdma_enabled(text, result.returncode)
    if enabled is True:
        detail = "RDMA enabled (OS); Recovery-only to change"
    elif enabled is False:
        detail = "RDMA disabled — enable via Recovery: rdma_ctl enable"
    else:
        detail = text[:200] if text else f"exit={result.returncode}"
    return RdmaStatus(
        tool_available=True,
        enabled=enabled,
        raw=text[:500],
        detail=detail,
    )
