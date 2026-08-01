"""TOML dump roundtrip."""

from __future__ import annotations

from maccluster.config.dump import dump_toml
from maccluster.config.load import load_toml_text


def test_roundtrip(valid_4_toml: str):
    cfg = load_toml_text(valid_4_toml)
    text = dump_toml(cfg)
    cfg2 = load_toml_text(text)
    assert cfg2.name == cfg.name
    assert len(cfg2.nodes) == len(cfg.nodes)
    assert cfg2.subnet == cfg.subnet
