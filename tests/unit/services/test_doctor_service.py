"""Doctor service."""

from __future__ import annotations

from maccluster.services.doctor_service import run_doctor


def test_doctor_runs(fake_ctx):
    report = run_doctor(fake_ctx)
    ids = {f.check_id for f in report.findings}
    assert "config" in ids
    assert "self" in ids
    assert "peers" in ids
    assert report.exit_code in (0, 1, 3)


def test_doctor_wires_rdma_device_to_peer(fake_ctx, monkeypatch):
    from maccluster.domain.enums import CheckSeverity
    from maccluster.domain.models import RdmaStatus
    from maccluster.services import doctor_service

    monkeypatch.setattr(
        doctor_service,
        "probe_rdma",
        lambda runner: RdmaStatus(tool_available=True, enabled=True, detail="on"),
    )
    monkeypatch.setattr(doctor_service, "arep_status_json", lambda *a, **k: {"peers": []})
    report = run_doctor(fake_ctx)
    by_id = {f.check_id: f for f in report.findings}
    assert "rdma_no_device_to_peer" in by_id
    assert by_id["rdma_no_device_to_peer"].severity == CheckSeverity.WARN

    monkeypatch.setattr(
        doctor_service,
        "arep_status_json",
        lambda *a, **k: {"peers": [{"displayName": "mac-mini-b", "transportCapable": ["rdma"]}]},
    )
    ids = {f.check_id for f in run_doctor(fake_ctx).findings}
    assert "rdma_device_to_peer" in ids


def test_doctor_skips_arep_when_rdma_is_off(fake_ctx, monkeypatch):
    from maccluster.domain.models import RdmaStatus
    from maccluster.services import doctor_service

    calls: list[int] = []
    monkeypatch.setattr(
        doctor_service,
        "probe_rdma",
        lambda runner: RdmaStatus(tool_available=True, enabled=False),
    )
    monkeypatch.setattr(doctor_service, "arep_status_json", lambda *a, **k: calls.append(1))
    ids = {f.check_id for f in run_doctor(fake_ctx).findings}
    assert "rdma_device_to_peer" in ids
    assert calls == []
