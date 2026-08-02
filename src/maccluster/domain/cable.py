"""Thunderbolt cable / link quality for Mac mini clusters.

Observed link speed comes from macOS (system_profiler). Physical cable type is
not exposed as a brand string — we classify by **trained link rate** and optional
device Mode (Thunderbolt 3/4/5 / USB4).

Studio fleet guidance (Apple Silicon Mac mini mesh over TB/USB4):
- **40 Gb/s** (TB4 / USB4 40G / many TB5 links train here): **recommended** for
  2–4 node bridge mesh + home sync.
- **20 Gb/s**: works, but only half the pipe — often TB3 cable, long passive
  cable, or a display/hub hop. Acceptable for light use; upgrade for bulk sync.
- **< 20 Gb/s** or unknown while connected: investigate cable/port.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from maccluster.domain.enums import LinkState
from maccluster.domain.models import ThunderboltPort, ThunderboltSnapshot


class CableGrade(str, Enum):
    EXCELLENT = "excellent"  # >= 40 Gb/s link
    GOOD = "good"  # >= 20 Gb/s
    MARGINAL = "marginal"  # > 0 and < 20
    UNKNOWN = "unknown"
    DISCONNECTED = "disconnected"


# Targets for Apple Silicon mini cluster (Gbit/s link rate from OS)
IDEAL_LINK_GBPS = 40.0
MIN_OK_LINK_GBPS = 20.0

# iperf3 TCP throughput grades (Mbit/s) on a healthy 40G TB bridge path
IPERF_EXCELLENT_MBPS = 15_000.0  # ~15 Gbit/s+ real TCP
IPERF_GOOD_MBPS = 5_000.0
IPERF_OK_MBPS = 1_000.0


@dataclass(frozen=True)
class CableAssessment:
    """One physical/logical TB hop assessment."""

    receptacle_id: str
    peer_name: str | None
    link_speed_gbps: float | None
    peer_mode: str | None  # e.g. Thunderbolt 3 / USB4
    grade: CableGrade
    cable_hint: str
    good_enough_for_cluster: bool
    summary: str


@dataclass(frozen=True)
class ClusterCableReport:
    assessments: tuple[CableAssessment, ...]
    best_mac_peer_gbps: float | None
    overall_grade: CableGrade
    good_enough: bool
    summary: str
    recommendation: str


def grade_link_speed(gbps: float | None, *, connected: bool) -> CableGrade:
    if not connected:
        return CableGrade.DISCONNECTED
    if gbps is None:
        return CableGrade.UNKNOWN
    if gbps + 1e-6 >= IDEAL_LINK_GBPS:
        return CableGrade.EXCELLENT
    if gbps + 1e-6 >= MIN_OK_LINK_GBPS:
        return CableGrade.GOOD
    if gbps > 0:
        return CableGrade.MARGINAL
    return CableGrade.UNKNOWN


def cable_hint_for(gbps: float | None, peer_mode: str | None) -> str:
    mode = (peer_mode or "").lower()
    if gbps is not None and gbps >= 40:
        if "thunderbolt 5" in mode or "usb4 v2" in mode:
            return "TB5/USB4v2-class path (trained ≥40 Gb/s) — ideal for cluster"
        if "thunderbolt 4" in mode or "usb4" in mode:
            return "TB4/USB4 40 Gb/s cable (or better) — ideal for cluster"
        if "thunderbolt 3" in mode:
            return "Link at 40 Gb/s (TB3 mode on peer device string) — full dual-lane TB3/40G cable"
        return (
            "40 Gb/s trained link — use certified TB4/USB4 40G or TB5 cable (current path is good)"
        )
    if gbps is not None and gbps >= 20:
        return (
            "20 Gb/s trained link — often TB3/USB-C 20G or long passive cable; "
            "OK for cluster but upgrade to 40G TB4 cable for bulk sync"
        )
    if gbps is not None and gbps > 0:
        return "Sub-20 Gb/s — check for USB-only cable, damaged cable, or daisy-chain bottleneck"
    return "Link speed unknown — re-seat cable; prefer Apple/certified TB4 40G or TB5"


def assess_port(port: ThunderboltPort) -> CableAssessment:
    connected = port.link_state == LinkState.CONNECTED
    grade = grade_link_speed(port.link_speed_gbps, connected=connected)
    hint = cable_hint_for(port.link_speed_gbps, getattr(port, "peer_mode", None))
    good = grade in (CableGrade.EXCELLENT, CableGrade.GOOD)
    if not connected:
        summary = f"receptacle {port.receptacle_id}: no link"
        good = False
        hint = "no cable / no device"
    elif grade == CableGrade.EXCELLENT:
        summary = (
            f"receptacle {port.receptacle_id}: {port.link_speed_gbps:g} Gb/s "
            f"→ peer={port.peer_name or '?'} — excellent for cluster"
        )
    elif grade == CableGrade.GOOD:
        summary = (
            f"receptacle {port.receptacle_id}: {port.link_speed_gbps:g} Gb/s "
            f"→ peer={port.peer_name or '?'} — good enough (prefer 40G cable)"
        )
    else:
        summary = (
            f"receptacle {port.receptacle_id}: "
            f"{port.link_speed_gbps if port.link_speed_gbps is not None else '?'} Gb/s "
            f"→ peer={port.peer_name or '?'} — weak for cluster"
        )
    return CableAssessment(
        receptacle_id=port.receptacle_id,
        peer_name=port.peer_name,
        link_speed_gbps=port.link_speed_gbps,
        peer_mode=getattr(port, "peer_mode", None),
        grade=grade,
        cable_hint=hint,
        good_enough_for_cluster=good and connected,
        summary=summary,
    )


def is_mac_peer_name(name: str | None) -> bool:
    if not name:
        return False
    n = name.lower()
    return "mac" in n or n.startswith("cm-") or "mac16" in n or "mac15" in n


def assess_cluster_cables(tb: ThunderboltSnapshot | None) -> ClusterCableReport:
    if tb is None or not tb.ports:
        return ClusterCableReport(
            assessments=(),
            best_mac_peer_gbps=None,
            overall_grade=CableGrade.UNKNOWN,
            good_enough=False,
            summary="no Thunderbolt data",
            recommendation="run `maccluster tb` on Apple Silicon with TB ports",
        )
    assessments = tuple(assess_port(p) for p in tb.ports)
    mac_links = [
        a
        for a in assessments
        if a.grade != CableGrade.DISCONNECTED and is_mac_peer_name(a.peer_name)
    ]
    # Also treat connected ports with Mac mini peer naming from profiler
    if not mac_links:
        mac_links = [
            a
            for a in assessments
            if a.grade not in (CableGrade.DISCONNECTED,)
            and a.peer_name
            and a.peer_name.lower() not in ("studio display",)
            and "ssd" not in (a.peer_name or "").lower()
            and "display" not in (a.peer_name or "").lower()
        ]
    speeds = [a.link_speed_gbps for a in mac_links if a.link_speed_gbps is not None]
    best = max(speeds) if speeds else None
    if best is not None and best >= IDEAL_LINK_GBPS:
        overall = CableGrade.EXCELLENT
        good = True
        summary = f"Mac↔Mac TB link {best:g} Gb/s — cable path is excellent for cluster"
        rec = "Keep using a certified Thunderbolt 4 (40 Gb/s) or Thunderbolt 5 cable; current link is good enough."
    elif best is not None and best >= MIN_OK_LINK_GBPS:
        overall = CableGrade.GOOD
        good = True
        summary = f"Mac↔Mac TB link {best:g} Gb/s — good enough; 40G cable recommended"
        rec = (
            "Cluster will work. For faster sync/bench, use a short certified TB4 40 Gb/s "
            "(or TB5) cable direct mini-to-mini, not via display/hub."
        )
    elif best is not None:
        overall = CableGrade.MARGINAL
        good = False
        summary = f"Mac↔Mac TB link only {best:g} Gb/s — below cluster target"
        rec = "Replace cable with TB4/USB4 40G or TB5; avoid USB3-only USB-C cables."
    else:
        # any connected excellent?
        connected = [a for a in assessments if a.grade != CableGrade.DISCONNECTED]
        if any(a.grade == CableGrade.EXCELLENT for a in connected):
            overall = CableGrade.EXCELLENT
            good = True
            best = max(
                (a.link_speed_gbps for a in connected if a.link_speed_gbps is not None),
                default=None,
            )
            summary = f"TB links present (best {best:g} Gb/s)" if best else "TB links present"
            rec = "Identify which receptacle is the peer Mac; prefer 40G path for mesh."
        else:
            overall = CableGrade.UNKNOWN
            good = False
            summary = "no clear Mac↔Mac TB link speed"
            rec = "Connect minis with TB4/TB5 cable; check `maccluster tb` and `maccluster status`."

    return ClusterCableReport(
        assessments=assessments,
        best_mac_peer_gbps=best,
        overall_grade=overall,
        good_enough=good,
        summary=summary,
        recommendation=rec,
    )


def grade_iperf_mbps(mbps: float | None) -> CableGrade:
    if mbps is None:
        return CableGrade.UNKNOWN
    if mbps >= IPERF_EXCELLENT_MBPS:
        return CableGrade.EXCELLENT
    if mbps >= IPERF_GOOD_MBPS:
        return CableGrade.GOOD
    if mbps >= IPERF_OK_MBPS:
        return CableGrade.MARGINAL  # usable but soft
    return CableGrade.MARGINAL


def iperf_verdict(mbps: float | None, *, link_gbps: float | None) -> str:
    if mbps is None:
        return "iperf3 failed or not available"
    g = grade_iperf_mbps(mbps)
    link_note = f" (link advertises {link_gbps:g} Gb/s)" if link_gbps else ""
    if g == CableGrade.EXCELLENT:
        return f"{mbps:.0f} Mbit/s TCP — excellent throughput{link_note}"
    if g == CableGrade.GOOD:
        return f"{mbps:.0f} Mbit/s TCP — good for cluster sync{link_note}"
    if mbps >= IPERF_OK_MBPS:
        return f"{mbps:.0f} Mbit/s TCP — OK but below 40G potential{link_note}"
    return f"{mbps:.0f} Mbit/s TCP — weak; check cable, CPU, or iperf server{link_note}"
