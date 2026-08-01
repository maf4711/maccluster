"""Init template."""

from __future__ import annotations

from maccluster.config.init_template import build_init_config
from maccluster.config.validate import validate_config
from maccluster.domain.models import HostIdentity


def test_build_four_nodes():
    ident = HostIdentity(
        hostname="mac-mini-a",
        hostnames=("mac-mini-a", "mac-mini-a.local"),
        hw_uuid="AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE",
    )
    cfg = build_init_config(ident, node_count=4)
    assert len(cfg.nodes) == 4
    assert str(cfg.subnet) == "10.42.0.0/24"
    assert cfg.nodes[0].hw_uuid == ident.hw_uuid
    assert validate_config(cfg) == []
