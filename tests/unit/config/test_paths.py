"""Config path resolution."""

from __future__ import annotations

from pathlib import Path

from maccluster.config.paths import resolve_config_path


def test_cli_wins(tmp_path: Path):
    p = tmp_path / "c.toml"
    got = resolve_config_path(p, env={"MACCLUSTER_CONFIG": "/tmp/other.toml"})
    assert got == p.resolve()


def test_env(tmp_path: Path):
    p = tmp_path / "env.toml"
    got = resolve_config_path(None, env={"MACCLUSTER_CONFIG": str(p)})
    assert got == p.resolve()


def test_default():
    got = resolve_config_path(None, env={})
    assert got.name == "cluster.toml"
    assert "maccluster" in str(got)
