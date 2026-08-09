"""Plist template."""

from __future__ import annotations

from maccluster.adapters.plist_template import render_heal_plist


def test_plist_contains_args():
    xml = render_heal_plist(
        label="com.maccluster.heal",
        program="/usr/local/bin/maccluster",
        config_path="/tmp/maccluster-test/cluster.toml",
        throttle_interval=30,
    )
    assert "com.maccluster.heal" in xml
    assert "/usr/local/bin/maccluster" in xml
    assert "heal" in xml
    assert "--loop" in xml
    assert "KeepAlive" in xml
    assert "<integer>30</integer>" in xml
