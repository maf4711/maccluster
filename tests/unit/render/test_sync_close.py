"""The sync Abschluss always names what moved, where it went, and how much."""

from __future__ import annotations

from pathlib import Path

from maccluster.commands.sync_cmd import _render_plain
from maccluster.domain.models import SyncHomeResult, SyncPeerResult, SyncRootMove
from maccluster.render.sync_close import transfer_block, transfer_block_from_log
from maccluster.services.sync_history import format_last_run, read_last_run, write_run_log


def _peer(**kw) -> SyncPeerResult:
    base = dict(
        peer_id="node-b",
        peer_ip="10.42.0.2",
        ssh_target="a321@10.42.0.2",
        push_rc=0,
        pull_rc=0,
        ok=True,
        via="tb",
    )
    base.update(kw)
    return SyncPeerResult(**base)


def test_close_names_what_where_and_how_much_even_when_nothing_moved():
    text = transfer_block(
        SyncHomeResult(
            local_home="/Users/a321",
            dry_run=False,
            strategy="newer",
            peers=(_peer(),),
            includes=("Documents/", "Desktop/"),
            target="home",
        )
    )
    assert text.splitlines()[0] == "transfer:"
    assert "what: Documents/, Desktop/" in text
    assert "where: node-b (a321@10.42.0.2) via tb" in text
    assert "how much: push 0 files, 0 B; pull 0 files, 0 B" in text


def test_close_uses_human_bytes_and_one_leg_per_root():
    text = transfer_block(
        SyncHomeResult(
            local_home="/Users/a321",
            dry_run=False,
            strategy="docsync",
            peers=(
                _peer(
                    push_files=3,
                    push_bytes=4_200_000,
                    pull_files=1,
                    pull_bytes=20,
                    transport="tb",
                    roots=(
                        SyncRootMove("documents", push_files=2, push_bytes=4_000_000),
                        SyncRootMove(
                            "desktop", push_files=1, push_bytes=200_000, pull_files=1, pull_bytes=20
                        ),
                    ),
                ),
            ),
            includes=("Documents/", "Desktop/"),
        )
    )
    assert "what: documents" in text
    assert "what: desktop" in text
    assert text.count("where: node-b (a321@10.42.0.2) via tb") == 2
    assert "push 2 files, 4.00 MB" in text
    assert "push 1 file, 200.00 KB" in text
    assert "pull 1 file, 20 B" in text


def test_dry_run_is_labelled_planned_and_still_reports_the_leg():
    text = _render_plain(
        SyncHomeResult(
            local_home="/tmp/home",
            dry_run=True,
            strategy="newer",
            peers=(_peer(push_files=4, push_bytes=1000),),
            target="home",
        )
    )
    assert text.strip().endswith("how much: push 4 files, 1.00 KB; pull 0 files, 0 B")
    assert "transfer: planned (nothing written)" in text
    assert "what: home (/tmp/home)" in text


def test_no_peer_still_has_the_three_lines():
    text = transfer_block(
        SyncHomeResult(local_home="/tmp/home", dry_run=False, strategy="newer", peers=())
    )
    assert "what: (none)" in text
    assert "where: (no peer)" in text
    assert "how much: push 0 files, 0 B; pull 0 files, 0 B" in text


def test_last_run_keeps_the_close_including_roots(tmp_path: Path):
    result = SyncHomeResult(
        local_home=str(tmp_path),
        dry_run=False,
        strategy="docsync",
        includes=("Documents/",),
        peers=(
            _peer(
                roots=(SyncRootMove("documents", push_files=7, push_bytes=3_500_000_000),),
                push_files=7,
                push_bytes=3_500_000_000,
            ),
        ),
    )
    write_run_log(result, log_dir=tmp_path / "logs")
    data = read_last_run(log_dir=tmp_path / "logs")
    assert data["peers"][0]["roots"] == [
        {
            "root": "documents",
            "push_files": 7,
            "push_bytes": 3_500_000_000,
            "pull_files": 0,
            "pull_bytes": 0,
        }
    ]
    text = format_last_run(data)
    assert "what: documents" in text
    assert "where: node-b (a321@10.42.0.2) via tb" in text
    assert "how much: push 7 files, 3.50 GB; pull 0 files, 0 B" in text


def test_old_log_without_roots_still_closes():
    text = transfer_block_from_log(
        {"peers": [{"peer_id": "node-c", "peer_ip": "10.42.0.3", "ok": True, "push_files": 1}]}
    )
    assert "what:" in text and "where: node-c" in text
    assert "how much: push 1 file, 0 B; pull 0 files, 0 B" in text
