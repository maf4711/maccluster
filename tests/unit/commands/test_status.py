"""status command."""

from __future__ import annotations

from types import SimpleNamespace

from maccluster.commands import status


def test_status_cmd(fake_ctx, capsys):
    code = status.run(fake_ctx, SimpleNamespace())
    assert code in (0, 3)
    assert "cluster:" in capsys.readouterr().out
