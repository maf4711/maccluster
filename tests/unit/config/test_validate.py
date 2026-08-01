"""Config validation."""

from __future__ import annotations

from pathlib import Path

from maccluster.config.load import load_toml_text
from maccluster.config.validate import validate_config


def test_valid(valid_4_toml: str):
    cfg = load_toml_text(valid_4_toml)
    assert validate_config(cfg) == []


def test_one_node(fixtures_dir: Path):
    text = (fixtures_dir / "configs" / "invalid_1_node.toml").read_text()
    cfg = load_toml_text(text)
    errors = validate_config(cfg)
    assert any("2–4" in e or "nodes" in e for e in errors)


def test_dup_ip(fixtures_dir: Path):
    text = (fixtures_dir / "configs" / "invalid_dup_ip.toml").read_text()
    cfg = load_toml_text(text)
    errors = validate_config(cfg)
    assert any("duplicate" in e and "ip" in e for e in errors)
