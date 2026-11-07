"""Sync history persists the ladder rung and downgrades; `sync --last` shows them."""

from __future__ import annotations

from pathlib import Path

from maccluster.domain.models import SyncHomeResult, SyncPeerResult
from maccluster.services.sync_history import format_last_run, read_last_run, write_run_log


def _result(tmp_path: Path) -> SyncHomeResult:
    return SyncHomeResult(
        local_home=str(tmp_path),
        dry_run=False,
        strategy="newer",
        peers=(
            SyncPeerResult(
                peer_id="node-b",
                peer_ip="10.42.0.2",
                ssh_target="a@10.42.0.2",
                push_rc=0,
                pull_rc=0,
                ok=True,
                transport="tb",
                downgrades=("transport downgrade rdma→tb: arep exit 1",),
            ),
            SyncPeerResult(
                peer_id="node-c",
                peer_ip="10.42.0.3",
                ssh_target="a@10.42.0.3",
                push_rc=0,
                pull_rc=0,
                ok=True,
                transport="rdma",
            ),
        ),
        transport_priority=("rdma", "tb", "wifi"),
    )


def test_run_log_persists_transport_and_downgrades(tmp_path: Path):
    write_run_log(_result(tmp_path), log_dir=tmp_path / "logs")
    data = read_last_run(log_dir=tmp_path / "logs")
    assert data is not None
    b, c = data["peers"]
    assert b["transport"] == "tb"
    assert b["downgrades"] == ["transport downgrade rdma→tb: arep exit 1"]
    assert c["transport"] == "rdma" and c["downgrades"] == []
    assert data["transport_priority"] == ["rdma", "tb", "wifi"]


def test_format_last_run_shows_transport_and_downgrades(tmp_path: Path):
    write_run_log(_result(tmp_path), log_dir=tmp_path / "logs")
    text = format_last_run(read_last_run(log_dir=tmp_path / "logs"))
    lines = text.splitlines()
    line_b = next(ln for ln in lines if "node-b" in ln)
    assert "transport=tb" in line_b
    assert any("transport downgrade rdma→tb: arep exit 1" in ln for ln in lines)
    assert "transport=rdma" in next(ln for ln in lines if "node-c" in ln)
    assert "priority=rdma→tb→wifi" in text


def test_format_last_run_tolerates_old_logs_without_transport():
    text = format_last_run({"peers": [{"peer_id": "node-b", "ok": True}]})
    assert "node-b" in text and "transport=" not in text
