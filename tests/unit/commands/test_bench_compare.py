"""bench --compare command: last-vs-best from history, no network."""

from __future__ import annotations

import json
from types import SimpleNamespace

from maccluster.cli.exit_codes import DEGRADED, OK
from maccluster.commands import bench
from maccluster.services.bench_history import (
    BENCH_HISTORY_ENV,
    BenchSample,
    append_samples,
    read_samples,
)


def _seed(path):
    append_samples(
        [
            BenchSample(
                ts="2026-08-01T00:00:00+00:00",
                peer="node-b",
                transport="tb",
                mbps=38000.0,
                source="bench",
            ),
            BenchSample(
                ts="2026-08-02T00:00:00+00:00",
                peer="node-b",
                transport="tb",
                mbps=30000.0,
                source="bench",
            ),
            BenchSample(
                ts="2026-08-02T00:00:00+00:00",
                peer="node-c",
                transport="rdma",
                mbps=50000.0,
                source="mesh",
            ),
        ],
        path=path,
    )


def test_compare_prints_table_and_flags_regression(fake_ctx, tmp_path, monkeypatch, capsys):
    hist = tmp_path / "h.jsonl"
    monkeypatch.setenv(BENCH_HISTORY_ENV, str(hist))
    _seed(hist)
    code = bench.run(fake_ctx, SimpleNamespace(compare=True, target=None, mesh=False, peer=None))
    out = capsys.readouterr().out
    assert code == DEGRADED
    assert "node-b" in out and "node-c" in out
    assert "REGRESSION" in out
    assert "-21.1%" in out


def test_compare_peer_filter_ok(fake_ctx, tmp_path, monkeypatch, capsys):
    hist = tmp_path / "h.jsonl"
    monkeypatch.setenv(BENCH_HISTORY_ENV, str(hist))
    _seed(hist)
    code = bench.run(
        fake_ctx, SimpleNamespace(compare=True, target=None, mesh=False, peer="node-c")
    )
    out = capsys.readouterr().out
    assert code == OK
    assert "node-c" in out and "node-b" not in out


def test_compare_json(fake_ctx, tmp_path, monkeypatch, capsys):
    hist = tmp_path / "h.jsonl"
    monkeypatch.setenv(BENCH_HISTORY_ENV, str(hist))
    _seed(hist)
    fake_ctx.json_mode = True
    code = bench.run(fake_ctx, SimpleNamespace(compare=True, target=None, mesh=False, peer=None))
    data = json.loads(capsys.readouterr().out)
    assert code == DEGRADED
    assert data["command"] == "bench"
    rows = data["data"]["rows"]
    assert {(r["peer"], r["transport"], r["regression"]) for r in rows} == {
        ("node-b", "tb", True),
        ("node-c", "rdma", False),
    }
    assert data["data"]["threshold_pct"] == 15.0


def test_compare_empty_history(fake_ctx, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv(BENCH_HISTORY_ENV, str(tmp_path / "missing.jsonl"))
    code = bench.run(fake_ctx, SimpleNamespace(compare=True, target=None, mesh=False, peer=None))
    assert code == OK
    assert "no bench history" in capsys.readouterr().out


def test_single_target_bench_records_history(fake_ctx, tmp_path, monkeypatch, capsys):
    hist = tmp_path / "h.jsonl"
    monkeypatch.setenv(BENCH_HISTORY_ENV, str(hist))
    code = bench.run(fake_ctx, SimpleNamespace(target="10.42.0.2", duration=1))
    assert code == OK
    got = read_samples(path=hist)
    assert len(got) == 1
    assert got[0].peer == "node-b"  # resolved from cluster.toml, not the raw IP
    assert got[0].transport == "tb"
    assert got[0].source == "bench"
    assert got[0].mbps > 0
