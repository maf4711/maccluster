"""Platform guard: macOS + arm64 for mutate; skippable for tests."""

from __future__ import annotations

import os

from maccluster.domain.models import PlatformInfo
from maccluster.errors import PlatformError


def platform_guard_skipped() -> bool:
    return os.environ.get("MACCLUSTER_SKIP_PLATFORM_GUARD", "").strip() in (
        "1",
        "true",
        "yes",
    )


def assert_supported_for_mutate(info: PlatformInfo) -> None:
    """Raise PlatformError (exit 2) if host cannot run mutate ops."""
    if platform_guard_skipped():
        return
    if not info.is_macos:
        raise PlatformError(
            "maccluster mutate requires macOS (Apple Silicon Mac mini); "
            "set MACCLUSTER_SKIP_PLATFORM_GUARD=1 only for tests",
            exit_code=2,
        )
    if not info.is_arm64:
        raise PlatformError(
            "maccluster mutate requires Apple Silicon (arm64); Intel Macs are not supported in v1",
            exit_code=2,
        )


def assert_supported_for_read(info: PlatformInfo) -> None:
    """Soft check for read-only commands — warn via exception only on totally wrong OS if desired.

    Read-only commands may run under test skip; on non-macOS production we still allow
    help/config but TB probes will fail naturally.
    """
    if platform_guard_skipped():
        return
    # Read-only does not hard-fail platform — probes handle missing tools.
    _ = info
