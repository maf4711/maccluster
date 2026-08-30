"""check_power: peer sleep/powernap settings (the node-b sleep=1 regression class)."""

from __future__ import annotations

from pathlib import Path

from maccluster.doctor_logic.checks import check_power
from maccluster.doctor_logic.host_parse import parse_pmset_power, snapshot_from_raw
from maccluster.domain.models import HostSnapshot

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "pmset_g"


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _snap(
    node_id: str = "node-b",
    *,
    sleep_minutes: int | None = None,
    powernap_enabled: bool | None = None,
    error: str | None = None,
) -> HostSnapshot:
    return HostSnapshot(
        node_id=node_id,
        ram_used_gb=None,
        ram_free_gb=None,
        load_1m=None,
        disk_free_gb=None,
        cpu_speed_limit_pct=None,
        ntp_offset_s=None,
        error=error,
        sleep_minutes=sleep_minutes,
        powernap_enabled=powernap_enabled,
    )


# --- parser ---


def test_parse_pmset_power_ok_fixture():
    assert parse_pmset_power(_fixture("ok.txt")) == (120, False)


def test_parse_pmset_power_powernap_fixture():
    assert parse_pmset_power(_fixture("warn_powernap.txt")) == (120, True)


def test_parse_pmset_power_short_sleep_with_suffix_fixture():
    # `sleep 1 (sleep prevented by ...)` — the node-b value that broke the cluster daily
    assert parse_pmset_power(_fixture("warn_sleep_short.txt")) == (1, False)


def test_parse_pmset_power_ignores_disksleep_displaysleep_and_power_button():
    text = " disksleep            10\n displaysleep         10\n Sleep On Power Button 1\n"
    assert parse_pmset_power(text) == (None, None)


def test_parse_pmset_power_empty():
    assert parse_pmset_power("") == (None, None)


def test_snapshot_from_raw_carries_power_settings():
    snap = snapshot_from_raw("node-b", pmset_g=_fixture("warn_powernap.txt"))
    assert snap.sleep_minutes == 120
    assert snap.powernap_enabled is True


def test_snapshot_from_raw_without_pmset_g_leaves_power_unknown():
    snap = snapshot_from_raw("node-b")
    assert snap.sleep_minutes is None
    assert snap.powernap_enabled is None


# --- check_power ---


def test_check_power_ok_scoped_to_node():
    f = check_power(_snap(sleep_minutes=120, powernap_enabled=False), peer=True)
    assert f.check_id == "power:node-b"
    assert f.severity.value == "ok"
    assert "sleep=120" in f.summary
    assert "powernap=0" in f.summary


def test_check_power_warns_on_short_sleep():
    f = check_power(_snap(sleep_minutes=1, powernap_enabled=False), peer=True)
    assert f.severity.value == "warn"
    assert "sleep=1" in f.summary


def test_check_power_warns_on_powernap():
    f = check_power(_snap(sleep_minutes=120, powernap_enabled=True), peer=True)
    assert f.severity.value == "warn"
    assert "powernap" in f.summary


def test_check_power_sleep_zero_means_never_sleeps_ok():
    f = check_power(_snap(sleep_minutes=0, powernap_enabled=False), peer=True)
    assert f.severity.value == "ok"


def test_check_power_boundary_30_ok_29_warn():
    ok = check_power(_snap(sleep_minutes=30, powernap_enabled=False), peer=True)
    warn = check_power(_snap(sleep_minutes=29, powernap_enabled=False), peer=True)
    assert ok.severity.value == "ok"
    assert warn.severity.value == "warn"


def test_check_power_unreadable_node_is_info():
    f = check_power(_snap(error="unreachable"), peer=True)
    assert f.check_id == "power:node-b"
    assert f.severity.value == "info"


def test_check_power_not_reported_is_info():
    f = check_power(_snap(), peer=True)
    assert f.severity.value == "info"


def test_check_power_not_a_snapshot_is_info():
    f = check_power(None)
    assert f.severity.value == "info"


def test_power_warn_is_cluster_relevant_and_degrades_exit():
    from maccluster.cli.exit_codes import DEGRADED
    from maccluster.doctor_logic.report import build_report

    f = check_power(_snap(sleep_minutes=1, powernap_enabled=False), peer=True)
    assert build_report([f]).exit_code == DEGRADED
