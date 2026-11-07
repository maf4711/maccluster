"""A partial local inventory must not drive a real transfer.

``plan_transfers`` is newest-wins bidirectional: a file the local walk never
reached is indistinguishable from a file that only exists on the peer, so it
gets pulled back over the local copy and the run still reports success. That
is a data-safety defect, so ``sync_home`` aborts unless the user opts in.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from test_sync_inventory_partial import _HangingWorker
from test_sync_service import RecordingRunner

from maccluster.errors import CliError
from maccluster.services import sync_inventory
from maccluster.services.sync_service import sync_home


def _home_with_one_file(tmp_path: Path) -> Path:
    home = tmp_path / "home"
    (home / "Documents").mkdir(parents=True)
    (home / "Documents" / "note.txt").write_text("local", encoding="utf-8")
    return home


def _sync_kwargs(home: Path) -> dict:
    return dict(
        peer="node-b",
        home=home,
        remote_home=str(home),
        user="a321",
        timeout=60,
        no_speedtest=True,
        write_log=False,
    )


def test_sync_home_refuses_to_transfer_from_a_partial_inventory(
    fake_ctx, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = _home_with_one_file(tmp_path)
    fake_ctx.runner = RecordingRunner()
    monkeypatch.setattr(sync_inventory, "ScandirWorker", _HangingWorker)
    with pytest.raises(CliError) as exc:
        sync_home(fake_ctx, **_sync_kwargs(home), dry_run=False)
    assert exc.value.exit_code != 0
    assert "PARTIAL" in exc.value.message.upper()
    assert "--allow-partial-inventory" in exc.value.message


def test_sync_home_partial_inventory_opt_in_proceeds(
    fake_ctx, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = _home_with_one_file(tmp_path)
    fake_ctx.runner = RecordingRunner()
    monkeypatch.setattr(sync_inventory, "ScandirWorker", _HangingWorker)
    result = sync_home(fake_ctx, **_sync_kwargs(home), dry_run=False, allow_partial_inventory=True)
    assert result.local_inventory_partial is True
    assert result.peers[0].ok


def test_sync_home_dry_run_shows_partial_plan_labelled(
    fake_ctx, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from maccluster.commands.sync_cmd import _render_plain

    home = _home_with_one_file(tmp_path)
    fake_ctx.runner = RecordingRunner()
    monkeypatch.setattr(sync_inventory, "ScandirWorker", _HangingWorker)
    result = sync_home(fake_ctx, **_sync_kwargs(home), dry_run=True)
    assert result.local_inventory_partial is True
    assert "PARTIAL" in _render_plain(result)
