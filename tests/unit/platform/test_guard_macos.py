"""Platform guard."""

from __future__ import annotations

import os

import pytest

from maccluster.domain.models import PlatformInfo
from maccluster.errors import PlatformError
from maccluster.platform.guard import assert_supported_for_mutate


def test_guard_skip_env(monkeypatch):
    monkeypatch.setenv("MACCLUSTER_SKIP_PLATFORM_GUARD", "1")
    assert_supported_for_mutate(PlatformInfo(is_macos=False, is_arm64=False))


def test_guard_rejects_linux(monkeypatch):
    monkeypatch.delenv("MACCLUSTER_SKIP_PLATFORM_GUARD", raising=False)
    # force not skipped
    if os.environ.get("MACCLUSTER_SKIP_PLATFORM_GUARD"):
        pytest.skip("guard skip forced in environment")
    with pytest.raises(PlatformError):
        assert_supported_for_mutate(PlatformInfo(is_macos=False, is_arm64=True))
