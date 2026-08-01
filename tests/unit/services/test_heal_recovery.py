"""A-038: heal/up path restores missing bridge+IP (post-reboot style)."""

from __future__ import annotations

from ipaddress import IPv4Address

import pytest

from maccluster.domain.models import BridgeInterface
from maccluster.errors import DegradedError
from maccluster.services.mutate_service import ensure_local


def test_missing_bridge_triggers_ensure_apply(fake_ctx):
    # Simulate post-reboot: bridge gone, no TB link → apply then degraded (exit 3)
    fake_ctx.net_read.bridges["bridge0"] = BridgeInterface(
        name="bridge0",
        exists=False,
        admin_up=False,
        addresses=(),
    )
    with pytest.raises(DegradedError) as ei:
        ensure_local(fake_ctx)
    assert ei.value.exit_code == 3
    assert fake_ctx.net_apply.calls
    # ensure_bridge_and_ip (or equivalent) was requested for Self IP
    joined = " ".join(str(c) for c in fake_ctx.net_apply.calls).lower()
    assert "bridge0" in joined
    assert "10.42.0.1" in joined or str(IPv4Address("10.42.0.1")) in joined
