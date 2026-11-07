"""Pure parsers for doctor --host (16K pages, df, load, thermal, ntp)."""

from __future__ import annotations

from pathlib import Path

from maccluster.doctor_logic.host_parse import (
    parse_df_free_gb,
    parse_pmset_cpu_limit,
    parse_rdma_enabled,
    parse_sntp_offset_s,
    parse_uptime_load_1m,
    parse_vm_stat_ram_gb,
    snapshot_from_json,
    snapshot_from_raw,
)

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"


def test_vm_stat_16k_pages_not_times_four():
    text = (FIXTURES / "vm_stat" / "apple_silicon_16k.txt").read_text(encoding="utf-8")
    used, free = parse_vm_stat_ram_gb(text)
    page = 16384
    expected_used = (1000 + 1000) * page / (1024**3)
    expected_free = (4000 + 2000) * page / (1024**3)
    assert used == expected_used
    assert free == expected_free
    # Regression: never treat Apple Silicon pages as 4 KiB
    wrong_4k = (1000 + 1000) * 4096 / (1024**3)
    assert used != wrong_4k
    assert used == 4 * wrong_4k


def test_df_10_gib_available():
    text = (
        "Filesystem     512-blocks      Used Available Capacity  Mounted on\n"
        "/dev/disk3s1s1  488555536 400000000  20971520    95%    /\n"
    )
    # 20971520 * 512 = 10 GiB
    assert parse_df_free_gb(text) == 10.0


def test_uptime_macos_and_locale_comma():
    assert (
        parse_uptime_load_1m("13:08  up 4 days,  6:51, 7 users, load averages: 2.50 1.10 0.90")
        == 2.5
    )
    assert (
        parse_uptime_load_1m("13:08  up 4 days, 7 users, load averages: 8,83 12,22 14,69") == 8.83
    )


def test_pmset_speed_limit_and_absent():
    assert parse_pmset_cpu_limit("CPU_Speed_Limit         = 80\n") == 80
    notes = (
        "Note: No thermal warning level has been recorded\n"
        "Note: No performance warning level has been recorded\n"
    )
    assert parse_pmset_cpu_limit(notes) is None


def test_sntp_offset_from_parenthesized_field():
    text = (
        "sntp: Exchange failed: Timeout\n"
        "        offset: FFFFFFFFFFFFFFFF.FF7DC11600000000 (-0.001987393)\n"
        "         delay: 0000000000000000.171CCB6C00000000 (0.090283121)\n"
    )
    assert parse_sntp_offset_s(text) == -0.001987393


def test_snapshot_from_json_raw_and_numeric():
    raw = snapshot_from_json(
        "node-b",
        '{"vm_stat":"Mach Virtual Memory Statistics: (page size of 16384 bytes)\\n'
        "Pages free: 4000.\\nPages active: 1000.\\nPages inactive: 2000.\\n"
        'Pages wired down: 1000.\\n","df":"Filesystem 512-blocks Used Available Capacity Mounted on\\n'
        '/dev/disk3s1s1 1 1 20971520 1% /\\n","uptime":"load averages: 1.25 1.00 0.90",'
        '"pmset":"CPU_Speed_Limit = 100\\n","sntp":null,"sntp_missing":true}',
    )
    assert raw.node_id == "node-b"
    assert raw.ntp_missing is True
    assert raw.ram_used_gb is not None
    assert raw.disk_free_gb == 10.0
    assert raw.load_1m == 1.25
    assert raw.cpu_speed_limit_pct == 100

    numeric = snapshot_from_json(
        "node-c",
        '{"ram_used_gb":8.5,"ram_free_gb":16.0,"load_1m":0.4,'
        '"disk_free_gb":120.0,"cpu_speed_limit_pct":null,"ntp_offset_s":0.01}',
    )
    assert numeric.ram_used_gb == 8.5
    assert numeric.ntp_offset_s == 0.01
    assert numeric.cpu_speed_limit_pct is None


def test_snapshot_from_raw_error_when_empty():
    snap = snapshot_from_raw("self")
    assert snap.node_id == "self"
    assert snap.ram_used_gb is None
    assert snap.error is None


def test_parse_rdma_enabled_disabled_and_ambiguous():
    assert parse_rdma_enabled("RDMA: enabled\n") is True
    assert parse_rdma_enabled("RDMA: disabled\n") is False
    assert parse_rdma_enabled("enabled") is True
    assert parse_rdma_enabled("disabled") is False
    assert parse_rdma_enabled("") is None
    assert parse_rdma_enabled("garbage output", returncode=1) is None
    assert parse_rdma_enabled("some enable-ish text", returncode=0) is True


def test_snapshot_from_raw_rdma_not_probed_by_default():
    snap = snapshot_from_raw("self")
    assert snap.rdma_tool_available is None
    assert snap.rdma_enabled is None


def test_snapshot_from_raw_rdma_enabled():
    snap = snapshot_from_raw("node-b", rdma="RDMA: enabled\n", rdma_missing=False)
    assert snap.rdma_tool_available is True
    assert snap.rdma_enabled is True


def test_snapshot_from_raw_rdma_disabled():
    snap = snapshot_from_raw("node-b", rdma="RDMA: disabled\n", rdma_missing=False)
    assert snap.rdma_tool_available is True
    assert snap.rdma_enabled is False


def test_snapshot_from_raw_rdma_tool_missing():
    snap = snapshot_from_raw("node-b", rdma=None, rdma_missing=True)
    assert snap.rdma_tool_available is False
    assert snap.rdma_enabled is None


def test_snapshot_from_json_raw_branch_carries_rdma():
    payload = (
        '{"vm_stat":"","df":"","uptime":"","pmset":"",'
        '"sntp":null,"sntp_missing":true,'
        '"rdma":"RDMA: enabled\\n","rdma_missing":false}'
    )
    snap = snapshot_from_json("node-b", payload)
    assert snap.rdma_tool_available is True
    assert snap.rdma_enabled is True


def test_snapshot_from_json_raw_branch_without_rdma_key_is_not_probed():
    # Older/other remote payloads without rdma keys must not fake tool_available.
    payload = '{"vm_stat":"","df":"","uptime":"","pmset":"","sntp":null,"sntp_missing":true}'
    snap = snapshot_from_json("node-b", payload)
    assert snap.rdma_tool_available is None
    assert snap.rdma_enabled is None


def test_snapshot_from_json_numeric_branch_carries_rdma():
    payload = (
        '{"ram_used_gb":8.5,"ram_free_gb":16.0,"load_1m":0.4,'
        '"disk_free_gb":120.0,"cpu_speed_limit_pct":null,"ntp_offset_s":0.01,'
        '"rdma_tool_available":true,"rdma_enabled":false}'
    )
    snap = snapshot_from_json("node-c", payload)
    assert snap.rdma_tool_available is True
    assert snap.rdma_enabled is False
