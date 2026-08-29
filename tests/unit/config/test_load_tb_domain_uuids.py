"""Optional per-node tb_domain_uuids in cluster.toml."""

from __future__ import annotations

from maccluster.config.load import load_toml_text

TOML = """
schema_version = 1
name = "t"
subnet = "10.42.0.0/24"
bridge_interface = "bridge0"

[[nodes]]
id = "node-a"
hostnames = ["mini-a.local"]
ip = "10.42.0.1"
hw_uuid = "409C591A-0000-0000-0000-000000000001"
tb_domain_uuids = [
  "E9F38DFF-9A9A-4A0A-8D9C-02C3325633C0",
  "2D9DB209-A8AE-4EC6-B7F5-38F5960A04C5",
]

[[nodes]]
id = "node-b"
hostnames = ["mini-b.local"]
ip = "10.42.0.2"
hw_uuid = "409C591A-0000-0000-0000-000000000002"
"""


def test_tb_domain_uuids_parsed_and_optional():
    cfg = load_toml_text(TOML)
    a, b = cfg.nodes
    assert a.tb_domain_uuids == (
        "E9F38DFF-9A9A-4A0A-8D9C-02C3325633C0",
        "2D9DB209-A8AE-4EC6-B7F5-38F5960A04C5",
    )
    assert b.tb_domain_uuids == ()
