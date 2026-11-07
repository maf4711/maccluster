"""arep's ~/.autoreplikator/bench-history.jsonl folded into bench --compare and doctor.

The fixture ``tests/fixtures/arep/bench_history_sample.jsonl`` is generated from
the arep schema ``{ts, peer fingerprint+name, transport rdma|tcp, bytes,
seconds, mbps}`` — the real file did not exist on this Mac yet when the
integration landed.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from maccluster.doctor_logic.checks import check_arep_bench_history
from maccluster.domain.enums import CheckSeverity
from maccluster.services.bench_history import (
    AREP_BENCH_HISTORY_ENV,
    AREP_STALE_AFTER_DAYS,
    BenchSample,
    append_samples,
    arep_history_age_days,
    compare_last_vs_best,
    default_arep_history_path,
    format_compare,
    read_all_samples,
    read_arep_samples,
)
from maccluster.services.doctor_service import run_doctor

NOW = datetime(2026, 8, 29, 0, 0, 0, tzinfo=UTC)


def _fixture(fixtures_dir: Path) -> Path:
    return fixtures_dir / "arep" / "bench_history_sample.jsonl"


def _arep_line(ts: str, *, name: str = "mac-mini-b", transport: str = "rdma") -> str:
    return json.dumps(
        {
            "ts": ts,
            "peer": {"fingerprint": "SHA256:bbbb", "name": name},
            "transport": transport,
            "bytes": 1000000000,
            "seconds": 2.0,
            "mbps": 4000.0,
        }
    )


# --- path resolution ---------------------------------------------------------


def test_default_arep_path_under_home(monkeypatch):
    monkeypatch.delenv(AREP_BENCH_HISTORY_ENV, raising=False)
    p = default_arep_history_path(env={})
    assert p == Path.home() / ".autoreplikator" / "bench-history.jsonl"


def test_env_overrides_arep_path(tmp_path):
    want = tmp_path / "a.jsonl"
    assert default_arep_history_path(env={AREP_BENCH_HISTORY_ENV: str(want)}) == want


# --- reading + normalisation ---------------------------------------------------


def test_read_arep_fixture_normalises_and_skips_malformed(fixtures_dir):
    got = read_arep_samples(path=_fixture(fixtures_dir))
    assert [(s.peer, s.transport, s.mbps) for s in got] == [
        ("mac-mini-b", "rdma", 40000.4),
        ("mac-mini-b", "tcp", 17179.9),
        ("mac-mini-c", "tcp", 4000.0),  # no mbps in line → bytes*8/seconds/1e6
        ("SHA256:ddddddddddddddddddddddddddddddddddddddddddd", "rdma", 0.0008),
        ("mac-mini-e", "tcp", 100.0),  # peer as plain string is tolerated
    ]
    assert {s.source for s in got} == {"arep"}
    assert all(s.retransmits is None for s in got)
    assert got[0].ts == "2026-08-28T10:00:00Z"
    assert got[0].duration_s == 4  # seconds rounded
    assert got[1].duration_s == 2


def test_read_arep_missing_file_and_env_default(tmp_path, monkeypatch, fixtures_dir):
    assert read_arep_samples(path=tmp_path / "none.jsonl") == []
    monkeypatch.setenv(AREP_BENCH_HISTORY_ENV, str(_fixture(fixtures_dir)))
    assert len(read_arep_samples()) == 5


def test_read_arep_accepts_displayname_key(tmp_path):
    p = tmp_path / "a.jsonl"
    line = {
        "ts": "2026-08-28T10:00:00Z",
        "peer": {"fingerprint": "SHA256:bb", "displayName": "mac-mini-b"},
        "transport": "tcp",
        "bytes": 1000000000,
        "seconds": 2.0,
        "mbps": 4000.0,
    }
    p.write_text(json.dumps(line) + "\n", encoding="utf-8")
    got = read_arep_samples(path=p)
    assert [(s.peer, s.transport) for s in got] == [("mac-mini-b", "tcp")]


def test_read_all_merges_iperf_and_arep(tmp_path, fixtures_dir):
    hist = tmp_path / "h.jsonl"
    append_samples(
        [
            BenchSample(
                ts="2026-08-28T09:00:00+00:00",
                peer="node-b",
                transport="tb",
                mbps=30000.0,
                source="bench",
            )
        ],
        path=hist,
    )
    got = read_all_samples(path=hist, arep_path=_fixture(fixtures_dir))
    assert {s.source for s in got} == {"bench", "arep"}
    assert len(got) == 6


# --- compare: arep and iperf side by side --------------------------------------


def test_compare_separates_arep_from_iperf_and_shows_source():
    samples = [
        BenchSample(
            ts="2026-08-01T00:00:00+00:00",
            peer="node-b",
            transport="rdma",
            mbps=30000.0,
            source="bench",
        ),
        BenchSample(
            ts="2026-08-02T00:00:00+00:00",
            peer="node-b",
            transport="rdma",
            mbps=40000.0,
            source="arep",
        ),
    ]
    rows = compare_last_vs_best(samples)
    assert [(r.peer, r.transport, r.source, r.samples) for r in rows] == [
        ("node-b", "rdma", "arep", 1),
        ("node-b", "rdma", "iperf", 1),
    ]
    text = format_compare(rows)
    assert "source" in text
    assert "arep" in text and "iperf" in text


# --- staleness --------------------------------------------------------------


def test_arep_history_age_days(tmp_path):
    p = tmp_path / "a.jsonl"
    assert arep_history_age_days(NOW, path=p) is None  # no file → no age
    p.write_text(
        _arep_line("2026-08-20T00:00:00Z") + "\n" + _arep_line("2026-08-27T00:00:00Z") + "\n",
        encoding="utf-8",
    )
    assert arep_history_age_days(NOW, path=p) == 2.0  # newest sample counts
    p.write_text(_arep_line("not-a-timestamp") + "\n", encoding="utf-8")
    assert arep_history_age_days(NOW, path=p) is None


def test_check_arep_bench_history_severity():
    assert check_arep_bench_history(None) is None
    fresh = check_arep_bench_history(2.0)
    assert fresh is not None
    assert fresh.check_id == "arep_bench"
    assert fresh.severity == CheckSeverity.OK
    boundary = check_arep_bench_history(7.0)  # exactly 7d is not > 7d
    assert boundary is not None and boundary.severity == CheckSeverity.OK
    stale = check_arep_bench_history(9.4)
    assert stale is not None
    assert stale.severity == CheckSeverity.INFO
    assert "stale" in stale.summary
    assert "9d" in stale.summary
    assert AREP_STALE_AFTER_DAYS == 7.0


def test_doctor_info_when_arep_history_stale(fake_ctx, tmp_path, monkeypatch):
    p = tmp_path / "arep-bench.jsonl"  # FakeClock now = 2026-08-01 → 31d old
    p.write_text(_arep_line("2026-07-01T00:00:00Z") + "\n", encoding="utf-8")
    monkeypatch.setenv(AREP_BENCH_HISTORY_ENV, str(p))
    by_id = {f.check_id: f for f in run_doctor(fake_ctx).findings}
    assert by_id["arep_bench"].severity == CheckSeverity.INFO
    assert "stale" in by_id["arep_bench"].summary


def test_doctor_ok_when_arep_history_fresh(fake_ctx, tmp_path, monkeypatch):
    p = tmp_path / "arep-bench.jsonl"
    p.write_text(_arep_line("2026-08-01T00:00:00Z") + "\n", encoding="utf-8")
    monkeypatch.setenv(AREP_BENCH_HISTORY_ENV, str(p))
    by_id = {f.check_id: f for f in run_doctor(fake_ctx).findings}
    assert by_id["arep_bench"].severity == CheckSeverity.OK


def test_doctor_silent_without_arep_history(fake_ctx, tmp_path, monkeypatch):
    monkeypatch.setenv(AREP_BENCH_HISTORY_ENV, str(tmp_path / "missing.jsonl"))
    by_id = {f.check_id: f for f in run_doctor(fake_ctx).findings}
    assert "arep_bench" not in by_id
