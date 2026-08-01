"""Config load."""

from __future__ import annotations

import pytest

from maccluster.config.load import load_toml_text
from maccluster.errors import ConfigError


def test_load_valid(valid_4_toml: str):
    cfg = load_toml_text(valid_4_toml)
    assert cfg.schema_version == 1
    assert len(cfg.nodes) == 4
    assert str(cfg.subnet) == "10.42.0.0/24"


def test_missing_schema():
    with pytest.raises(ConfigError):
        load_toml_text('name="x"\nsubnet="10.42.0.0/24"\n[[nodes]]\nid="a"\n')


def test_bad_toml():
    with pytest.raises(ConfigError):
        load_toml_text("[[[not valid")
