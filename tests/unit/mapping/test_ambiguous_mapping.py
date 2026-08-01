"""A-039: ambiguous receptacle→interface mapping fails closed (no silent guess)."""

from __future__ import annotations

import pytest

from maccluster.domain.enums import LinkState
from maccluster.domain.models import ThunderboltPort, ThunderboltSnapshot
from maccluster.errors import ConfigError
from maccluster.mapping.receptacle import resolve_target_interface


def test_ambiguous_interfaces_fail_closed():
    tb = ThunderboltSnapshot(
        ports=(
            ThunderboltPort(
                receptacle_id="1",
                interface_name="en1",
                capable=True,
                thunderbolt_version="USB4",
                link_speed_gbps=40.0,
                link_state=LinkState.CONNECTED,
            ),
            ThunderboltPort(
                receptacle_id="2",
                interface_name="en2",
                capable=True,
                thunderbolt_version="USB4",
                link_speed_gbps=40.0,
                link_state=LinkState.CONNECTED,
            ),
        ),
        source="fixture",
    )
    # config bridge not present among available ifaces → must not pick silently
    with pytest.raises(ConfigError) as ei:
        resolve_target_interface(
            config_bridge="bridge0",
            tb=tb,
            available_ifaces=("en1", "en2", "lo0"),
        )
    assert ei.value.exit_code == 2
    msg = ei.value.message.lower()
    assert "ambiguous" in msg or "fail closed" in msg
    assert "en1" in msg and "en2" in msg


def test_missing_bridge_and_empty_mapping_fail_closed():
    with pytest.raises(ConfigError) as ei:
        resolve_target_interface(
            config_bridge="bridge0",
            tb=None,
            available_ifaces=("lo0", "en0"),
        )
    assert ei.value.exit_code == 2
    assert "fail closed" in ei.value.message.lower() or "cannot resolve" in ei.value.message.lower()
