"""Config error cases."""

from __future__ import annotations

from pathlib import Path

import pytest

from maccluster.config.load import load_toml_text
from maccluster.errors import ConfigError


def test_no_schema(fixtures_dir: Path):
    text = (fixtures_dir / "configs" / "no_schema.toml").read_text()
    with pytest.raises(ConfigError) as ei:
        load_toml_text(text)
    assert "schema_version" in ei.value.message
