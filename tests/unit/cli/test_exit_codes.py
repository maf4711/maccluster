"""Exit code constants."""

from __future__ import annotations

from maccluster.cli import exit_codes


def test_exit_codes_values():
    assert exit_codes.OK == 0
    assert exit_codes.ERROR == 1
    assert exit_codes.USAGE == 2
    assert exit_codes.DEGRADED == 3
