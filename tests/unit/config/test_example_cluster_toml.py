"""examples/cluster.toml is valid."""

from __future__ import annotations

from pathlib import Path

from maccluster.config.load import load_toml_text
from maccluster.config.validate import validate_config

ROOT = Path(__file__).resolve().parents[3]


def test_example_valid():
    text = (ROOT / "examples" / "cluster.toml").read_text(encoding="utf-8")
    cfg = load_toml_text(text)
    assert len(cfg.nodes) == 4
    assert str(cfg.nodes[0].ip) == "10.42.0.1"
    assert str(cfg.nodes[3].ip) == "10.42.0.4"
    assert validate_config(cfg) == []
