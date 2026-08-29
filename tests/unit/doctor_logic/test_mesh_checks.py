"""Doctor mesh / rdma check findings."""

from __future__ import annotations

from maccluster.doctor_logic.checks import check_mesh, check_rdma, check_rdma_host
from maccluster.domain.enums import CheckSeverity, MeshVerdict
from maccluster.domain.models import HostSnapshot, MeshHealth, RdmaStatus


def test_check_mesh_partial_warn():
    m = MeshHealth(
        expected_peers=3,
        peers_up=1,
        peers_down=2,
        peers_unknown=0,
        fully_meshed=False,
        verdict=MeshVerdict.PARTIAL,
        summary="partial 1/3 peers up",
    )
    f = check_mesh(m)
    assert f.check_id == "mesh"
    assert f.severity == CheckSeverity.WARN


def test_check_rdma_enabled_ok():
    f = check_rdma(RdmaStatus(tool_available=True, enabled=True, detail="ok"))
    assert f.severity == CheckSeverity.OK


def test_check_rdma_missing_info():
    f = check_rdma(RdmaStatus(tool_available=False, enabled=None, detail="not found"))
    assert f.severity == CheckSeverity.INFO


def _host_snap(**overrides) -> HostSnapshot:
    base = dict(
        node_id="node-b",
        ram_used_gb=None,
        ram_free_gb=None,
        load_1m=None,
        disk_free_gb=None,
        cpu_speed_limit_pct=None,
        ntp_offset_s=None,
    )
    base.update(overrides)
    return HostSnapshot(**base)


def test_check_rdma_host_enabled_ok_scoped_to_node():
    f = check_rdma_host(_host_snap(rdma_tool_available=True, rdma_enabled=True), peer=True)
    assert f.check_id == "rdma:node-b"
    assert f.severity == CheckSeverity.OK
    assert "node-b" in f.summary


def test_check_rdma_host_disabled_is_info_not_warn():
    f = check_rdma_host(_host_snap(rdma_tool_available=True, rdma_enabled=False), peer=True)
    assert f.check_id == "rdma:node-b"
    assert f.severity == CheckSeverity.INFO
    assert "Recovery" in f.detail


def test_check_rdma_host_tool_unavailable_info():
    f = check_rdma_host(_host_snap(rdma_tool_available=False, rdma_enabled=None), peer=True)
    assert f.severity == CheckSeverity.INFO
    assert "unavailable" in f.summary


def test_check_rdma_host_not_probed_is_low_noise_info():
    f = check_rdma_host(_host_snap(), peer=False)
    assert f.check_id == "rdma"
    assert f.severity == CheckSeverity.INFO
