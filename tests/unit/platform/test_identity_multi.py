"""A-007: multiple self-match must fail closed with exit 2."""

from __future__ import annotations

from ipaddress import IPv4Address

import pytest

from maccluster.domain.models import HostIdentity, Node
from maccluster.errors import ConfigError
from maccluster.platform.identity import match_self


def test_multiple_hostname_matches_exit_2():
    nodes = (
        Node(
            id="node-a",
            hostnames=("shared.local", "mac-a"),
            ip=IPv4Address("10.42.0.1"),
            hw_uuid="00000000-0000-0000-0000-000000000001",
        ),
        Node(
            id="node-b",
            hostnames=("shared.local", "mac-b"),
            ip=IPv4Address("10.42.0.2"),
            hw_uuid="00000000-0000-0000-0000-000000000002",
        ),
    )
    ident = HostIdentity(
        hostname="shared.local",
        hostnames=("shared.local",),
        hw_uuid="ffffffff-ffff-ffff-ffff-ffffffffffff",
    )
    with pytest.raises(ConfigError) as ei:
        match_self(nodes, ident)
    assert ei.value.exit_code == 2
    assert "multiple" in ei.value.message.lower()
    assert "node-a" in ei.value.message and "node-b" in ei.value.message
