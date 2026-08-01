"""Mapping fail-closed."""

from __future__ import annotations

import pytest

from maccluster.errors import ConfigError
from maccluster.mapping.receptacle import resolve_target_interface


def test_prefers_config_when_present():
    iface = resolve_target_interface(
        config_bridge="bridge0",
        tb=None,
        available_ifaces=("lo0", "en0", "bridge0"),
    )
    assert iface == "bridge0"


def test_invalid_iface():
    with pytest.raises(ConfigError):
        resolve_target_interface(config_bridge="bad iface!", tb=None, available_ifaces=None)
