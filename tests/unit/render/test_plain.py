"""Plain render."""

from __future__ import annotations

from maccluster.adapters.tb_ioreg import FakeTB
from maccluster.render.plain import render_tb


def test_render_tb():
    text = render_tb(FakeTB().probe())
    assert "receptacle" in text
    assert "NO-LINK" in text or "unconnected" in text
