"""Throughput history for the RDMA rollout: bench/speedtest samples as JSONL.

Every bench, mesh bench and speedtest appends one line per measured peer to
``~/.local/state/maccluster/bench-history.jsonl`` (path injectable, or via
``MACCLUSTER_BENCH_HISTORY``). ``arep bench`` keeps its own history at
``~/.autoreplikator/bench-history.jsonl`` (``{ts, peer fingerprint+name,
transport rdma|tcp, bytes, seconds, mbps}``); that file is read too and
normalised into the same :class:`BenchSample` shape with ``source="arep"``.
``bench --compare`` folds both logs into last-vs-best per (peer, transport,
source) and marks regressions > 15 %.

Aggregation is pure; nothing here touches the network.
"""

from __future__ import annotations

import json
import math
import os
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from maccluster.app_factory import AppContext
from maccluster.cli.exit_codes import DEGRADED, OK
from maccluster.constants import CONFIG_DIR_NAME
from maccluster.domain.models import (
    TRANSPORT_NAMES,
    BenchResult,
    MeshBenchReport,
    SpeedtestReport,
)

BENCH_HISTORY_ENV = "MACCLUSTER_BENCH_HISTORY"
BENCH_HISTORY_FILE_NAME = "bench-history.jsonl"
AREP_BENCH_HISTORY_ENV = "MACCLUSTER_AREP_HISTORY"
AREP_HISTORY_DIR_NAME = ".autoreplikator"
AREP_SOURCE = "arep"
AREP_STALE_AFTER_DAYS = 7.0
REGRESSION_THRESHOLD_PCT = 15.0
DEFAULT_TRANSPORT = "tb"


@dataclass(frozen=True)
class BenchSample:
    """One throughput measurement toward one peer over one transport."""

    ts: str
    peer: str  # node id; "<peer>→self" for reverse, "<a>→<b>" for peer↔peer mesh paths
    transport: str  # tb | rdma | wifi (own history) | rdma | tcp (arep history)
    mbps: float
    source: str  # bench | mesh | speedtest (all iperf-based) | arep
    retransmits: int | None = None
    duration_s: int | None = None


@dataclass(frozen=True)
class CompareRow:
    peer: str
    transport: str
    source: str  # arep | iperf
    last_mbps: float
    best_mbps: float
    delta_pct: float  # (last - best) / best * 100, never > 0
    regression: bool
    samples: int
    last_ts: str


# --- paths / transport ------------------------------------------------------------


def default_bench_history_path(env: Mapping[str, str] | None = None) -> Path:
    environ = env if env is not None else os.environ
    override = (environ.get(BENCH_HISTORY_ENV) or "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / ".local" / "state" / CONFIG_DIR_NAME / BENCH_HISTORY_FILE_NAME


def default_arep_history_path(env: Mapping[str, str] | None = None) -> Path:
    """Where ``arep bench`` writes its own history (never written by maccluster)."""
    environ = env if env is not None else os.environ
    override = (environ.get(AREP_BENCH_HISTORY_ENV) or "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / AREP_HISTORY_DIR_NAME / BENCH_HISTORY_FILE_NAME


def transport_of(obj: Any, default: str = DEFAULT_TRANSPORT) -> str:
    """Transport rung a result was measured over.

    Reads a ``transport`` field when present (SyncHomeResult grows one in the
    RDMA workstream), then a per-peer ``via``; anything unknown is the bridge.
    """
    for attr in ("transport", "via"):
        value = getattr(obj, attr, None)
        if isinstance(value, str) and value in TRANSPORT_NAMES:
            return value
    return default


def _now() -> str:
    return datetime.now(UTC).isoformat()


# --- JSONL store ------------------------------------------------------------------


def append_samples(
    samples: Iterable[BenchSample],
    *,
    path: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> Path:
    """Append one JSON line per sample (creates parent dirs). Raises OSError."""
    p = path or default_bench_history_path(env)
    rows = [json.dumps(asdict(s), sort_keys=True) for s in samples]
    if not rows:
        return p
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as fh:
        fh.write("\n".join(rows) + "\n")
    return p


def record_samples(samples: Sequence[BenchSample], *, path: Path | None = None) -> Path | None:
    """Best-effort append: a broken history must never fail a bench."""
    if not samples:
        return None
    try:
        return append_samples(samples, path=path)
    except OSError:
        return None


def read_samples(
    *,
    path: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> list[BenchSample]:
    """Parse the history; corrupt or incomplete lines are skipped."""
    return _read_jsonl(path or default_bench_history_path(env), _parse_line)


def read_arep_samples(
    *,
    path: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> list[BenchSample]:
    """arep's bench history, normalised to BenchSample; malformed lines are skipped."""
    return _read_jsonl(path or default_arep_history_path(env), _parse_arep_line)


def read_all_samples(
    *,
    path: Path | None = None,
    arep_path: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> list[BenchSample]:
    """Own iperf-based history plus arep's bench history as one sample list."""
    return read_samples(path=path, env=env) + read_arep_samples(path=arep_path, env=env)


def _read_jsonl(p: Path, parse: Callable[[str], BenchSample | None]) -> list[BenchSample]:
    if not p.is_file():
        return []
    try:
        text = p.read_text(encoding="utf-8")
    except OSError:
        return []
    return [s for line in text.splitlines() if (s := parse(line)) is not None]


def _parse_line(line: str) -> BenchSample | None:
    line = line.strip()
    if not line:
        return None
    try:
        raw = json.loads(line)
        return BenchSample(
            ts=str(raw["ts"]),
            peer=str(raw["peer"]),
            transport=str(raw.get("transport") or DEFAULT_TRANSPORT),
            mbps=float(raw["mbps"]),
            source=str(raw.get("source") or "bench"),
            retransmits=raw.get("retransmits"),
            duration_s=raw.get("duration_s"),
        )
    except (ValueError, TypeError, KeyError, AttributeError):
        return None


def _parse_arep_line(line: str) -> BenchSample | None:
    """One arep entry: {ts, peer fingerprint+name, transport rdma|tcp, bytes, seconds, mbps}.

    The peer may be an object (``name``/``displayName`` preferred, fingerprint
    as fallback label) or a plain string. A missing ``mbps`` is derived from
    ``bytes``/``seconds``. Anything else malformed skips the line.
    """
    line = line.strip()
    if not line:
        return None
    try:
        raw = json.loads(line)
    except ValueError:
        return None
    if not isinstance(raw, dict):
        return None
    peer = _arep_peer_label(raw.get("peer"))
    ts = raw.get("ts")
    transport = raw.get("transport")
    mbps = _arep_mbps(raw)
    if peer is None or mbps is None or not isinstance(ts, str) or not ts:
        return None
    if not isinstance(transport, str) or not transport.strip():
        return None
    return BenchSample(
        ts=ts,
        peer=peer,
        transport=transport.strip().lower(),
        mbps=mbps,
        source=AREP_SOURCE,
        retransmits=None,
        duration_s=_arep_duration(raw.get("seconds")),
    )


def _arep_peer_label(raw: Any) -> str | None:
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    if isinstance(raw, dict):
        for key in ("name", "displayName", "fingerprint"):
            value = raw.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def _finite(value: Any) -> float | None:
    if isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value):
        return float(value)
    return None


def _arep_mbps(raw: Mapping[str, Any]) -> float | None:
    mbps = _finite(raw.get("mbps"))
    if mbps is not None and mbps >= 0:
        return mbps
    size = _finite(raw.get("bytes"))
    seconds = _finite(raw.get("seconds"))
    if size is not None and size >= 0 and seconds is not None and seconds > 0:
        return size * 8.0 / seconds / 1e6
    return None


def _arep_duration(seconds: Any) -> int | None:
    value = _finite(seconds)
    return int(round(value)) if value is not None and value > 0 else None


# --- sample builders ----------------------------------------------------------------


def samples_from_bench(
    result: BenchResult,
    *,
    peer: str | None,
    transport: str | None = None,
    duration_s: int | None = None,
) -> list[BenchSample]:
    if not result.success or result.mbps is None:
        return []
    return [
        BenchSample(
            ts=_now(),
            peer=peer or result.target,
            transport=transport or transport_of(result),
            mbps=float(result.mbps),
            source="bench",
            retransmits=result.retransmits,
            duration_s=duration_s,
        )
    ]


def samples_from_mesh(
    report: MeshBenchReport,
    *,
    self_id: str,
    transport: str | None = None,
) -> list[BenchSample]:
    rung = transport or transport_of(report)
    ts = _now()
    out: list[BenchSample] = []
    for p in report.paths:
        if not p.ok or p.mbps is None:
            continue
        if p.src_id == self_id:
            label = p.dst_id
        elif p.dst_id == self_id:
            label = f"{p.src_id}→self"
        else:
            label = f"{p.src_id}→{p.dst_id}"
        out.append(
            BenchSample(
                ts=ts,
                peer=label,
                transport=rung,
                mbps=float(p.mbps),
                source="mesh",
                retransmits=p.retransmits,
                duration_s=report.duration_s,
            )
        )
    return out


def samples_from_speedtest(
    report: SpeedtestReport,
    *,
    transport: str | None = None,
) -> list[BenchSample]:
    rung = transport or transport_of(report)
    ts = _now()
    return [
        BenchSample(
            ts=ts,
            peer=p.peer_id,
            transport=rung,
            mbps=float(p.iperf_mbps),
            source="speedtest",
            duration_s=report.duration_s,
        )
        for p in report.peers
        if p.iperf_ok and p.iperf_mbps is not None  # "(no peer)" placeholder is never ok
    ]


def record_bench_result(
    ctx: AppContext,
    result: BenchResult,
    *,
    duration_s: int | None = None,
    path: Path | None = None,
) -> Path | None:
    """Append a single-target bench, keyed by node id when the target is in cluster.toml."""
    return record_samples(
        samples_from_bench(result, peer=_peer_label(ctx, result.target), duration_s=duration_s),
        path=path,
    )


def _peer_label(ctx: AppContext, target: str) -> str:
    from maccluster.services.config_service import load_and_bind_self

    try:
        cfg, _ = load_and_bind_self(ctx)
    except Exception:
        return target
    for n in cfg.nodes:
        if target in (n.id, str(n.ip)):
            return n.id
    return target


# --- staleness (doctor) -------------------------------------------------------------


def arep_history_age_days(
    now: datetime,
    *,
    path: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> float | None:
    """Days since the newest arep bench sample; None without a readable history."""
    newest: datetime | None = None
    for s in read_arep_samples(path=path, env=env):
        t = _parse_ts(s.ts)
        if t is not None and (newest is None or t > newest):
            newest = t
    if newest is None:
        return None
    return (now - newest).total_seconds() / 86400.0


def _parse_ts(ts: str) -> datetime | None:
    try:
        t = datetime.fromisoformat(ts)
    except ValueError:
        return None
    return t if t.tzinfo is not None else t.replace(tzinfo=UTC)


# --- pure aggregation ---------------------------------------------------------------


def source_class(source: str) -> str:
    """Compare bucket for a sample source: arep vs maccluster's iperf-based ones."""
    return AREP_SOURCE if source == AREP_SOURCE else "iperf"


def compare_last_vs_best(
    samples: Iterable[BenchSample],
    *,
    threshold_pct: float = REGRESSION_THRESHOLD_PCT,
    peer: str | None = None,
) -> list[CompareRow]:
    """Per (peer, transport, source): latest vs. best ever; regression if it lost > threshold.

    arep and iperf samples never share a bucket — the two measure differently.
    """
    groups: dict[tuple[str, str, str], list[BenchSample]] = {}
    for s in samples:
        if peer and s.peer != peer:
            continue
        groups.setdefault((s.peer, s.transport, source_class(s.source)), []).append(s)
    rows: list[CompareRow] = []
    for (peer_id, transport, source), items in sorted(groups.items()):
        last = max(items, key=lambda s: s.ts)  # ISO-8601 UTC sorts lexically
        best = max(s.mbps for s in items)
        delta = 0.0 if best <= 0 else (last.mbps - best) / best * 100.0
        rows.append(
            CompareRow(
                peer=peer_id,
                transport=transport,
                source=source,
                last_mbps=last.mbps,
                best_mbps=best,
                delta_pct=delta,
                regression=delta < -threshold_pct,
                samples=len(items),
                last_ts=last.ts,
            )
        )
    return rows


def exit_for_compare(rows: Sequence[CompareRow]) -> int:
    return DEGRADED if any(r.regression for r in rows) else OK


def compare_to_dict(
    rows: Sequence[CompareRow], *, threshold_pct: float = REGRESSION_THRESHOLD_PCT
) -> dict[str, Any]:
    return {
        "threshold_pct": threshold_pct,
        "regressions": sum(1 for r in rows if r.regression),
        "rows": [asdict(r) for r in rows],
    }


def format_compare(
    rows: Sequence[CompareRow], *, threshold_pct: float = REGRESSION_THRESHOLD_PCT
) -> str:
    if not rows:
        return (
            "no bench history yet "
            "(run: maccluster bench <peer> | bench --mesh | speedtest | arep bench)"
        )
    head = (
        f"=== bench history: last vs best per peer/transport (regression > {threshold_pct:g}%) ==="
    )
    width = max(len(r.peer) for r in rows)
    lines = [
        head,
        f"  {'peer':<{width}}  transport  source  {'last':>14}  {'best':>14}  {'delta':>7}"
        "  n    status",
    ]
    for r in rows:
        status = "REGRESSION" if r.regression else "ok"
        lines.append(
            f"  {r.peer:<{width}}  {r.transport:<9}  {r.source:<6}  {r.last_mbps:>9.0f} Mb/s  "
            f"{r.best_mbps:>9.0f} Mb/s  {r.delta_pct:>+6.1f}%  {r.samples:<4} {status}"
        )
    n_reg = sum(1 for r in rows if r.regression)
    lines.append(f"regressions: {n_reg}/{len(rows)}")
    return "\n".join(lines)
