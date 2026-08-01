"""tb command."""

from __future__ import annotations

from types import SimpleNamespace

from maccluster.commands import tb


def test_tb_cmd(fake_ctx, capsys):
    code = tb.run(fake_ctx, SimpleNamespace())
    assert code == 0
    out = capsys.readouterr().out
    assert "receptacle" in out or "Thunderbolt" in out
