"""status + speedtest report each peer's OWN negotiated TB link rate.

Regression for: every peer row printed link=80G (the machine's best Mac↔Mac
link) even though node-c's link only negotiated 40 Gb/s.
"""

from __future__ import annotations

from maccluster.services.speedtest_service import format_speedtest_report, run_speedtest
from maccluster.services.status_service import collect_status


def _by_id(items, node_id: str):
    for item in items:
        key = getattr(item, "peer_id", None) or item.node.id
        if key == node_id:
            return item
    raise AssertionError(f"{node_id} missing in {items!r}")


def test_status_reports_per_peer_link_rate(two_peer_ctx) -> None:
    snap, _code = collect_status(two_peer_ctx)
    peers = [nh for nh in snap.nodes if nh.node.id != snap.self_node_id]
    assert _by_id(peers, "node-b").link_speed_gbps == 80.0
    assert _by_id(peers, "node-c").link_speed_gbps == 40.0


def test_speedtest_reports_per_peer_link_rate(two_peer_ctx) -> None:
    report = run_speedtest(two_peer_ctx, skip_iperf=True)
    assert _by_id(report.peers, "node-b").link_speed_gbps == 80.0
    assert _by_id(report.peers, "node-c").link_speed_gbps == 40.0
    # Machine-best stays what it is — a machine-level number, not a peer row.
    assert report.best_link_gbps == 80.0


def test_speedtest_render_shows_both_rates(two_peer_ctx) -> None:
    text = format_speedtest_report(run_speedtest(two_peer_ctx, skip_iperf=True))
    assert "link=80G" in text
    assert "link=40G" in text


def test_speedtest_peer_filter_keeps_own_rate(two_peer_ctx) -> None:
    report = run_speedtest(two_peer_ctx, peer="node-c", skip_iperf=True)
    assert [p.peer_id for p in report.peers] == ["node-c"]
    assert report.peers[0].link_speed_gbps == 40.0
    # 40 Gb/s trains at the cluster target -> still good enough.
    assert report.peers[0].good_enough is True
