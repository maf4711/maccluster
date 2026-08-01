"""Service status readonly."""

from __future__ import annotations

from maccluster.services import service_mgmt


def test_status(fake_ctx):
    st = service_mgmt.service_status(fake_ctx)
    assert st.installed is False
