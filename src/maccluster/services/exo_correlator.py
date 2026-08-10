"""Optional correlation with local exo API (stdlib only, localhost)."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any

from maccluster.constants import EXO_DEFAULT_BASE_URL, EXO_PROBE_TIMEOUT_S
from maccluster.domain.models import ExoCorrelation


def probe_exo(
    *,
    base_url: str | None = None,
    timeout: float = EXO_PROBE_TIMEOUT_S,
    expected_nodes: int | None = None,
) -> ExoCorrelation:
    """GET {base}/state and summarize mesh/runners. Never mutates exo."""
    url_base = (base_url or EXO_DEFAULT_BASE_URL).rstrip("/")
    state_url = f"{url_base}/state"
    try:
        req = urllib.request.Request(
            state_url,
            headers={"Accept": "application/json", "User-Agent": "maccluster-exo-correlator"},
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 — localhost ops
            body = resp.read()
            http_ok = 200 <= getattr(resp, "status", 200) < 300
    except urllib.error.HTTPError as exc:
        return ExoCorrelation(
            probed=True,
            http_ok=False,
            base_url=url_base,
            summary=f"exo HTTP {exc.code}",
            error=str(exc.reason or exc),
        )
    except Exception as exc:
        return ExoCorrelation(
            probed=True,
            http_ok=False,
            base_url=url_base,
            summary="exo unreachable (daemon down or not installed)",
            error=str(exc)[:200],
        )

    try:
        data = json.loads(body.decode("utf-8", errors="replace"))
    except (json.JSONDecodeError, UnicodeError) as exc:
        return ExoCorrelation(
            probed=True,
            http_ok=http_ok,
            base_url=url_base,
            summary="exo /state not JSON",
            error=str(exc),
        )

    return _summarize(data, base_url=url_base, expected_nodes=expected_nodes)


def _summarize(
    data: dict[str, Any],
    *,
    base_url: str,
    expected_nodes: int | None,
) -> ExoCorrelation:
    topo = data.get("topology") if isinstance(data.get("topology"), dict) else {}
    nodes = topo.get("nodes")
    if isinstance(nodes, list):
        topology_nodes = len(nodes)
    elif isinstance(nodes, dict):
        topology_nodes = len(nodes)
    else:
        topology_nodes = None

    stale_seconds = _max_last_seen_age(data.get("lastSeen"))
    runners = _len_map(data.get("runners"))
    downloads = _download_count(data.get("downloads"))
    rdma_nodes = _rdma_enabled_count(data.get("nodeRdmaCtl"))
    instances_summary = _instances_summary(data)

    mesh_ok: bool | None = None
    if topology_nodes is not None and expected_nodes is not None and expected_nodes > 0:
        # exo counts self in topology.nodes; expected_nodes is full cluster size
        mesh_ok = topology_nodes >= expected_nodes and (
            stale_seconds is None or stale_seconds < 60
        )
    elif topology_nodes is not None:
        mesh_ok = topology_nodes >= 2 and (stale_seconds is None or stale_seconds < 60)

    parts = [f"exo=up"]
    if topology_nodes is not None:
        parts.append(f"mesh={topology_nodes}")
    if stale_seconds is not None:
        parts.append(f"stale={stale_seconds:.0f}s")
    if runners is not None:
        parts.append(f"runners={runners}")
    if downloads is not None:
        parts.append(f"dl={downloads}")
    if rdma_nodes is not None:
        parts.append(f"rdma_nodes={rdma_nodes}")
    if mesh_ok is False:
        parts.append("WARN: http-alive but mesh incomplete/stale")
    elif mesh_ok is True:
        parts.append("mesh-ok")

    return ExoCorrelation(
        probed=True,
        http_ok=True,
        base_url=base_url,
        topology_nodes=topology_nodes,
        stale_seconds=stale_seconds,
        runners=runners,
        downloads=downloads,
        rdma_enabled_nodes=rdma_nodes,
        instances_summary=instances_summary,
        mesh_ok=mesh_ok,
        summary=" ".join(parts),
        error=None,
    )


def _len_map(val: Any) -> int | None:
    if isinstance(val, dict):
        return len(val)
    if isinstance(val, list):
        return len(val)
    return None


def _download_count(val: Any) -> int | None:
    if not isinstance(val, dict):
        return None
    total = 0
    for v in val.values():
        if isinstance(v, (list, dict)):
            total += len(v)
        elif v:
            total += 1
    return total


def _rdma_enabled_count(val: Any) -> int | None:
    if not isinstance(val, dict):
        return None
    n = 0
    for v in val.values():
        if isinstance(v, dict) and v.get("enabled") is True:
            n += 1
        elif v is True:
            n += 1
    return n


def _max_last_seen_age(last_seen: Any) -> float | None:
    if not isinstance(last_seen, dict) or not last_seen:
        return None
    now = datetime.now(timezone.utc).timestamp()
    ages: list[float] = []
    for v in last_seen.values():
        if not isinstance(v, str):
            continue
        ts = _parse_iso(v)
        if ts is not None:
            ages.append(max(0.0, now - ts))
    return max(ages) if ages else None


def _parse_iso(s: str) -> float | None:
    text = s.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text).timestamp()
    except ValueError:
        # Strip fractional seconds if needed: 2026-01-01T00:00:00.123+00:00
        if "." in text:
            try:
                head, frac = text.split(".", 1)
                tz = ""
                for i, ch in enumerate(frac):
                    if ch in "+-" or ch == "Z":
                        tz = frac[i:].replace("Z", "+00:00")
                        break
                return datetime.fromisoformat(head + tz).timestamp()
            except ValueError:
                return None
        return None


def _instances_summary(data: dict[str, Any]) -> str:
    instances = data.get("instances")
    if not isinstance(instances, dict) or not instances:
        return "no active instances"
    bits: list[str] = []
    for inst in list(instances.values())[:5]:
        if not isinstance(inst, dict):
            continue
        # MlxRingInstance nesting or flat
        mlx = inst.get("MlxRingInstance") if isinstance(inst.get("MlxRingInstance"), dict) else inst
        shards = mlx.get("shardAssignments") if isinstance(mlx.get("shardAssignments"), dict) else {}
        model = shards.get("modelId") or mlx.get("modelId") or "?"
        if isinstance(model, str) and "/" in model:
            model = model.rsplit("/", 1)[-1]
        hosts = mlx.get("hostsByNode")
        n_hosts = len(hosts) if isinstance(hosts, dict) else "?"
        bits.append(f"{model}@{n_hosts}n")
    return ", ".join(bits) if bits else f"{len(instances)} instance(s)"
