"""Platform guard skip for tests."""

from __future__ import annotations

from maccluster.platform.guard import platform_guard_skipped


def test_skip_enabled_in_ci():
    assert platform_guard_skipped() is True
