"""Thunderbolt probe orchestration."""

from __future__ import annotations

from maccluster.app_factory import AppContext
from maccluster.domain.models import ThunderboltSnapshot
from maccluster.errors import CliError


def probe_tb(ctx: AppContext) -> ThunderboltSnapshot:
    try:
        return ctx.tb.probe()
    except CliError:
        raise
    except Exception as exc:
        raise CliError(f"thunderbolt probe failed: {exc}", exit_code=1) from exc
