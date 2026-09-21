"""Heal heartbeat write/read + stale detection."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from maccluster.services.heal_heartbeat import read_heartbeat, write_heartbeat


@pytest.mark.parametrize(
    "payload",
    [
        "[]",
        "null",
        '{"ts": Infinity}',
        '{"ts": NaN}',
        '{"ts": 100, "interval_seconds": -5}',
        '{"ts": 100, "interval_seconds": Infinity}',
    ],
)
def test_invalid_heartbeat_is_stale(tmp_path, payload):
    path = tmp_path / "heartbeat.json"
    path.write_text(payload)
    result = read_heartbeat(path=path, now=101)
    assert result.stale
    assert "unreadable" in result.detail


def test_fresh_heartbeat(tmp_path: Path):
    p = tmp_path / "hb.json"
    write_heartbeat(ok=True, exit_code=0, interval_seconds=30, path=p)
    hb = read_heartbeat(path=p, interval_seconds=30, now=time.time())
    assert not hb.stale
    assert hb.last_ok is True
    assert hb.last_exit_code == 0


def test_stale_heartbeat(tmp_path: Path):
    p = tmp_path / "hb.json"
    write_heartbeat(ok=True, exit_code=0, interval_seconds=10, path=p)
    # age >> 3 * interval
    hb = read_heartbeat(path=p, interval_seconds=10, now=time.time() + 10_000)
    assert hb.stale


def test_missing_heartbeat(tmp_path: Path):
    p = tmp_path / "missing.json"
    hb = read_heartbeat(path=p, interval_seconds=30)
    assert hb.stale
    assert hb.age_seconds is None
