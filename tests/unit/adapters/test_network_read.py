"""ifconfig parser."""

from __future__ import annotations

from pathlib import Path

from maccluster.adapters.network_read import parse_ifconfig_interface


def test_parse_configured(fixtures_dir: Path):
    text = (fixtures_dir / "ifconfig" / "bridge0_configured.txt").read_text()
    b = parse_ifconfig_interface(text, "bridge0")
    assert b.exists
    assert b.admin_up
    assert any(str(a) == "10.42.0.1" for a in b.addresses)


def test_parse_empty(fixtures_dir: Path):
    text = (fixtures_dir / "ifconfig" / "bridge0_empty.txt").read_text()
    b = parse_ifconfig_interface(text, "bridge0")
    assert b.exists
    assert not b.admin_up
    assert b.addresses == ()


def test_missing():
    b = parse_ifconfig_interface("ifconfig: interface bridge9 does not exist", "bridge9")
    assert not b.exists
