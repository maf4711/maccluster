"""Doctor mesh / rdma check findings."""

from __future__ import annotations

from maccluster.doctor_logic.checks import check_mesh, check_rdma
from maccluster.domain.enums import CheckSeverity, MeshVerdict
from maccluster.domain.models import MeshHealth, RdmaStatus


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
