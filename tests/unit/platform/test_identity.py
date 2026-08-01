"""Self-match identity tests."""

from __future__ import annotations

from ipaddress import IPv4Address

import pytest

from maccluster.domain.models import HostIdentity, Node
from maccluster.errors import ConfigError
from maccluster.platform.identity import match_self


def _nodes():
    return (
        Node(
            id="node-a",
            hostnames=("mac-mini-a.local", "mac-mini-a"),
            ip=IPv4Address("10.42.0.1"),
            hw_uuid="00000000-0000-0000-0000-000000000001",
        ),
        Node(
            id="node-b",
            hostnames=("mac-mini-b.local", "mac-mini-b"),
            ip=IPv4Address("10.42.0.2"),
            hw_uuid="00000000-0000-0000-0000-000000000002",
        ),
    )


def test_match_hostname():
    ident = HostIdentity(
        hostname="mac-mini-a",
        hostnames=("mac-mini-a", "mac-mini-a.local"),
        hw_uuid="FFFFFFFF-FFFF-FFFF-FFFF-FFFFFFFFFFFF",
    )
    n = match_self(_nodes(), ident)
    assert n.id == "node-a"


def test_match_uuid():
    ident = HostIdentity(
        hostname="unknown",
        hostnames=("unknown",),
        hw_uuid="00000000-0000-0000-0000-000000000002",
    )
    n = match_self(_nodes(), ident)
    assert n.id == "node-b"


def test_no_match():
    ident = HostIdentity(hostname="other", hostnames=("other",), hw_uuid="nope")
    with pytest.raises(ConfigError) as ei:
        match_self(_nodes(), ident)
    assert ei.value.exit_code == 2
