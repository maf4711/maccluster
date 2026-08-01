"""Service uninstall idempotent."""

from __future__ import annotations

from maccluster.services import service_mgmt


def test_uninstall_twice(fake_ctx):
    service_mgmt.uninstall_service(fake_ctx)
    st = service_mgmt.uninstall_service(fake_ctx)
    assert st.installed is False
