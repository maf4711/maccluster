"""Mutate uses fakes — privilege path."""

from __future__ import annotations

import pytest

from maccluster.domain.models import BridgeInterface
from maccluster.errors import PrivilegeError
from maccluster.services.mutate_service import ensure_local


def test_privilege_error(fake_ctx):
    fake_ctx.net_read.bridges["bridge0"] = BridgeInterface(
        name="bridge0", exists=True, admin_up=False, addresses=()
    )
    fake_ctx.net_apply.fail_privilege = True
    with pytest.raises(PrivilegeError) as ei:
        ensure_local(fake_ctx)
    assert ei.value.exit_code == 1
    assert "admin/sudo required" in ei.value.message
