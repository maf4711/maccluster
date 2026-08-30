"""bench_history: JSONL store + pure last-vs-best aggregation (no network)."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from maccluster.cli.exit_codes import DEGRADED, OK
from maccluster.domain.enums import BenchQuality
from maccluster.domain.models import (
    BenchResult,
    MeshBenchReport,
    MeshPathResult,
    SpeedtestPeerResult,
    SpeedtestReport,
)
from maccluster.services.bench_history import (
    BENCH_HISTORY_ENV,
    REGRESSION_THRESHOLD_PCT,
    BenchSample,
    append_samples,
    compare_last_vs_best,
    default_bench_history_path,
    exit_for_compare,
    format_compare,
    read_samples,
    samples_from_bench,
    samples_from_mesh,
    samples_from_speedtest,
    transport_of,
)


def _s(peer: str, mbps: float, *, transport: str = "tb", ts: str = "2026-08-29T10:00:00+00:00"):
    return BenchSample(ts=ts, peer=peer, transport=transport, mbps=mbps, source="bench")


# --- path resolution -------------------------------------------------------


def test_default_path_under_local_state(monkeypatch):
    monkeypatch.delenv(BENCH_HISTORY_ENV, raising=False)
    p = default_bench_history_path(env={})
    assert p == Path.home() / ".local" / "state" / "maccluster" / "bench-history.jsonl"


def test_env_overrides_default_path(tmp_path):
    want = tmp_path / "h.jsonl"
    assert default_bench_history_path(env={BENCH_HISTORY_ENV: str(want)}) == want


# --- transport ---------------------------------------------------------------


def test_transport_of_reads_field_else_tb():
    assert transport_of(SimpleNamespace(transport="rdma")) == "rdma"
    assert transport_of(SimpleNamespace(transport="wifi")) == "wifi"
    assert transport_of(SimpleNamespace()) == "tb"
    assert transport_of(SimpleNamespace(transport=None)) == "tb"
    assert transport_of(SimpleNamespace(transport="bogus")) == "tb"
    # SyncPeerResult-style `via` is honoured when no `transport` field exists
    assert transport_of(SimpleNamespace(via="wifi")) == "wifi"


# --- JSONL store -----------------------------------------------------------------


def test_append_and_read_roundtrip(tmp_path):
    path = tmp_path / "nested" / "bench-history.jsonl"
    append_samples([_s("node-b", 100.0), _s("node-c", 200.0, transport="rdma")], path=path)
    append_samples([_s("node-b", 90.0)], path=path)
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3
    row = json.loads(lines[0])
    assert row["peer"] == "node-b"
    assert row["transport"] == "tb"
    assert row["mbps"] == 100.0
    got = read_samples(path=path)
    assert [(x.peer, x.transport, x.mbps) for x in got] == [
        ("node-b", "tb", 100.0),
        ("node-c", "rdma", 200.0),
        ("node-b", "tb", 90.0),
    ]


def test_read_skips_corrupt_lines_and_missing_file(tmp_path):
    path = tmp_path / "h.jsonl"
    assert read_samples(path=path) == []
    path.write_text(
        'not json\n{"peer":"node-b"}\n'
        + json.dumps({"ts": "t", "peer": "node-b", "transport": "tb", "mbps": 5.0, "source": "x"})
        + "\n",
        encoding="utf-8",
    )
    got = read_samples(path=path)
    assert len(got) == 1
    assert got[0].mbps == 5.0


# --- sample builders -----------------------------------------------------------


def test_samples_from_bench_only_on_success():
    ok = BenchResult(target="10.42.0.2", mbps=1234.5, success=True, message="ok", retransmits=3)
    bad = BenchResult(target="10.42.0.2", mbps=None, success=False, message="fail")
    got = samples_from_bench(ok, peer="node-b", duration_s=5)
    assert len(got) == 1
    assert got[0].peer == "node-b"
    assert got[0].transport == "tb"
    assert got[0].mbps == 1234.5
    assert got[0].retransmits == 3
    assert got[0].source == "bench"
    assert got[0].duration_s == 5
    assert samples_from_bench(bad, peer="node-b") == []
    assert samples_from_bench(ok, peer=None)[0].peer == "10.42.0.2"


def _path(src: str, dst: str, mbps: float | None, ok: bool = True) -> MeshPathResult:
    return MeshPathResult(
        src_id=src,
        dst_id=dst,
        src_ip="10.42.0.1",
        dst_ip="10.42.0.2",
        mbps=mbps,
        retransmits=0,
        quality=BenchQuality.EXCELLENT,
        flags=(),
        ok=ok,
        message="ok",
    )


def test_samples_from_mesh_keys_by_other_endpoint():
    report = MeshBenchReport(
        bind_mode="tb-bridge",
        duration_s=2,
        orchestrated=True,
        busy_skipped=False,
        paths=(
            _path("node-a", "node-b", 37000.0),
            _path("node-b", "node-a", 36000.0),
            _path("node-b", "node-c", 35000.0),
            _path("node-a", "node-c", None, ok=False),
        ),
        summary="3/4 ok",
    )
    got = samples_from_mesh(report, self_id="node-a")
    assert [(x.peer, x.mbps) for x in got] == [
        ("node-b", 37000.0),
        ("node-b→self", 36000.0),
        ("node-b→node-c", 35000.0),
    ]
    assert {x.source for x in got} == {"mesh"}
    assert {x.transport for x in got} == {"tb"}
    assert samples_from_mesh(report, self_id="node-a", transport="rdma")[0].transport == "rdma"


def test_samples_from_speedtest_skips_failed_and_placeholder():
    def peer(pid: str, mbps: float | None, ok: bool) -> SpeedtestPeerResult:
        return SpeedtestPeerResult(
            peer_id=pid,
            peer_ip="-",
            link_speed_gbps=40.0,
            cable_grade="ideal",
            cable_summary="s",
            iperf_mbps=mbps,
            iperf_ok=ok,
            iperf_message="m",
            good_enough=True,
        )

    report = SpeedtestReport(
        cable_summary="s",
        cable_grade="ideal",
        cable_recommendation="r",
        best_link_gbps=40.0,
        good_enough=True,
        peers=(
            peer("node-b", 30000.0, True),
            peer("node-c", None, False),
            peer("(no peer)", None, False),
        ),
        duration_s=5,
    )
    got = samples_from_speedtest(report)
    assert [(x.peer, x.mbps, x.source) for x in got] == [("node-b", 30000.0, "speedtest")]


# --- pure aggregation ------------------------------------------------------------


def test_compare_last_vs_best_marks_regression():
    samples = [
        _s("node-b", 38000.0, ts="2026-08-01T00:00:00+00:00"),
        _s("node-b", 37000.0, ts="2026-08-02T00:00:00+00:00"),
        _s("node-b", 30000.0, ts="2026-08-03T00:00:00+00:00"),  # -21% → regression
        _s("node-c", 20000.0, ts="2026-08-01T00:00:00+00:00"),
        _s("node-c", 19000.0, ts="2026-08-02T00:00:00+00:00"),  # -5% → ok
        _s("node-b", 50000.0, transport="rdma", ts="2026-08-03T00:00:00+00:00"),
    ]
    rows = compare_last_vs_best(samples)
    assert [(r.peer, r.transport) for r in rows] == [
        ("node-b", "rdma"),
        ("node-b", "tb"),
        ("node-c", "tb"),
    ]
    b_tb = rows[1]
    assert b_tb.last_mbps == 30000.0
    assert b_tb.best_mbps == 38000.0
    assert round(b_tb.delta_pct, 2) == -21.05
    assert b_tb.regression is True
    assert b_tb.samples == 3
    assert b_tb.last_ts == "2026-08-03T00:00:00+00:00"
    c = rows[2]
    assert c.delta_pct == -5.0
    assert c.regression is False
    r = rows[0]
    assert r.last_mbps == r.best_mbps == 50000.0
    assert r.delta_pct == 0.0
    assert r.samples == 1


def test_compare_threshold_boundary_and_zero_best():
    exact = [_s("p", 100.0, ts="1"), _s("p", 85.0, ts="2")]
    assert compare_last_vs_best(exact)[0].regression is False  # exactly -15% is not > 15%
    just_over = [_s("p", 100.0, ts="1"), _s("p", 84.9, ts="2")]
    assert compare_last_vs_best(just_over)[0].regression is True
    custom = compare_last_vs_best(just_over, threshold_pct=20.0)
    assert custom[0].regression is False
    zero = compare_last_vs_best([_s("p", 0.0, ts="1")])
    assert zero[0].delta_pct == 0.0 and zero[0].regression is False
    assert REGRESSION_THRESHOLD_PCT == 15.0


def test_compare_last_is_latest_ts_not_append_order():
    samples = [
        _s("p", 50.0, ts="2026-08-05T00:00:00+00:00"),
        _s("p", 100.0, ts="2026-08-01T00:00:00+00:00"),
    ]
    row = compare_last_vs_best(samples)[0]
    assert row.last_mbps == 50.0
    assert row.best_mbps == 100.0


def test_compare_peer_filter_and_empty():
    samples = [_s("node-b", 1.0), _s("node-c", 2.0)]
    rows = compare_last_vs_best(samples, peer="node-c")
    assert [r.peer for r in rows] == ["node-c"]
    assert compare_last_vs_best([]) == []


def test_format_and_exit_for_compare():
    rows = compare_last_vs_best(
        [
            _s("node-b", 38000.0, ts="1"),
            _s("node-b", 30000.0, ts="2"),
            _s("node-c", 20000.0, ts="1", transport="rdma"),
        ]
    )
    text = format_compare(rows)
    assert "REGRESSION" in text
    assert "node-b" in text and "node-c" in text
    assert "rdma" in text and "tb" in text
    assert "-21.1%" in text
    assert "15%" in text
    assert exit_for_compare(rows) == DEGRADED
    assert exit_for_compare([r for r in rows if not r.regression]) == OK
    assert exit_for_compare([]) == OK
    assert "no bench history" in format_compare([])
