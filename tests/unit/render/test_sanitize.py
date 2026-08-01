"""Sanitize control chars."""

from __future__ import annotations

from maccluster.render.sanitize import sanitize


def test_strips_ansi_and_ctrl():
    raw = "host\x1b[31mname\x07"
    assert "\x1b" not in sanitize(raw)
    assert "\x07" not in sanitize(raw)
