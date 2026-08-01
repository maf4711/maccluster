"""Mutate service with fakes."""

from __future__ import annotations

import pytest

from maccluster.domain.models import BridgeInterface
from maccluster.errors import DegradedError
from maccluster.services.mutate_service import ensure_local


def test_already_configured_no_tb_link_degraded(fake_ctx):
    # FakeTB has unconnected ports; bridge already has IP
    with pytest.raises(DegradedError) as ei:
        ensure_local(fake_ctx)
    assert ei.value.exit_code == 3
    assert "no TB link" in ei.value.message


def test_apply_when_missing_ip(fake_ctx):
    fake_ctx.net_read.bridges["bridge0"] = BridgeInterface(
        name="bridge0",
        exists=True,
        admin_up=True,
        addresses=(),
    )
    # still no TB → degraded after apply
    with pytest.raises(DegradedError):
        ensure_local(fake_ctx)
    assert fake_ctx.net_apply.calls
