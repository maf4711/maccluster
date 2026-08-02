"""TB cable grading."""

from __future__ import annotations

from maccluster.domain.cable import (
    CableGrade,
    assess_cluster_cables,
    cable_hint_for,
    grade_link_speed,
    iperf_verdict,
)
from maccluster.domain.enums import LinkState
from maccluster.domain.models import ThunderboltPort, ThunderboltSnapshot


def _port(speed: float | None, peer: str, *, connected: bool = True) -> ThunderboltPort:
    return ThunderboltPort(
        receptacle_id="1",
        interface_name=None,
        capable=True,
        thunderbolt_version="USB4/TB",
        link_speed_gbps=speed,
        link_state=LinkState.CONNECTED if connected else LinkState.UNCONNECTED,
        peer_name=peer if connected else None,
        peer_mode="Thunderbolt 4" if speed and speed >= 40 else "Thunderbolt 3",
    )


def test_grade_40_excellent():
    assert grade_link_speed(40.0, connected=True) == CableGrade.EXCELLENT
    assert grade_link_speed(20.0, connected=True) == CableGrade.GOOD
    assert grade_link_speed(10.0, connected=True) == CableGrade.MARGINAL


def test_cluster_40g_mac_peer_good_enough():
    snap = ThunderboltSnapshot(
        ports=(
            _port(40.0, "Mac mini"),
            _port(20.0, "Studio Display"),
        ),
        source="test",
    )
    r = assess_cluster_cables(snap)
    assert r.best_mac_peer_gbps == 40.0
    assert r.overall_grade == CableGrade.EXCELLENT
    assert r.good_enough is True
    assert "excellent" in r.summary.lower() or "40" in r.summary


def test_20g_still_ok():
    snap = ThunderboltSnapshot(ports=(_port(20.0, "Mac mini"),), source="t")
    r = assess_cluster_cables(snap)
    assert r.overall_grade == CableGrade.GOOD
    assert r.good_enough is True


def test_hint_and_iperf():
    assert "40" in cable_hint_for(40.0, "Thunderbolt 4")
    assert "excellent" in iperf_verdict(20_000.0, link_gbps=40.0).lower()
