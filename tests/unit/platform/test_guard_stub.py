"""Platform guard stub with skip."""

from __future__ import annotations

from maccluster.domain.models import PlatformInfo
from maccluster.platform.guard import assert_supported_for_mutate, platform_guard_skipped


def test_skipped_in_tests():
    assert platform_guard_skipped() is True
    assert_supported_for_mutate(PlatformInfo(is_macos=False, is_arm64=False))
