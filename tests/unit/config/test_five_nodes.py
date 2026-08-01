"""A-006: hard limit 2–4 nodes — five nodes rejected, no silent truncate."""

from __future__ import annotations

from maccluster.config.load import load_toml_text
from maccluster.config.validate import validate_config

_FIVE = """
schema_version = 1
name = "too-many"
subnet = "10.42.0.0/24"
bridge_interface = "bridge0"

[[nodes]]
id = "n1"
hostnames = ["a"]
ip = "10.42.0.1"
hw_uuid = "u1"

[[nodes]]
id = "n2"
hostnames = ["b"]
ip = "10.42.0.2"
hw_uuid = "u2"

[[nodes]]
id = "n3"
hostnames = ["c"]
ip = "10.42.0.3"
hw_uuid = "u3"

[[nodes]]
id = "n4"
hostnames = ["d"]
ip = "10.42.0.4"
hw_uuid = "u4"

[[nodes]]
id = "n5"
hostnames = ["e"]
ip = "10.42.0.5"
hw_uuid = "u5"
"""


def test_five_nodes_rejected():
    cfg = load_toml_text(_FIVE)
    assert len(cfg.nodes) == 5  # loader does not truncate
    errors = validate_config(cfg)
    joined = " ".join(errors).lower()
    assert any("max 4" in e or "2–4" in e or "2-4" in e for e in errors)
    assert "5" in joined or "got 5" in joined
