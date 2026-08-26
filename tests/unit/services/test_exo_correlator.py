"""Exo /state summarizer (no live network)."""

from __future__ import annotations

from datetime import UTC, datetime

from maccluster.services.exo_correlator import _summarize


def test_summarize_healthy_mesh():
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    data = {
        "topology": {"nodes": ["a", "b", "c", "d"]},
        "lastSeen": {"a": now, "b": now, "c": now, "d": now},
        "runners": {"r1": {}},
        "downloads": {},
        "nodeRdmaCtl": {"a": {"enabled": True}, "b": {"enabled": False}},
        "instances": {},
    }
    exo = _summarize(data, base_url="http://127.0.0.1:52415", expected_nodes=4)
    assert exo.http_ok
    assert exo.topology_nodes == 4
    assert exo.mesh_ok is True
    assert exo.runners == 1
    assert exo.rdma_enabled_nodes == 1
    assert "mesh-ok" in exo.summary


def test_summarize_solo_mesh_warn():
    data = {
        "topology": {"nodes": ["only-me"]},
        "lastSeen": {},
        "runners": {},
        "instances": {},
    }
    exo = _summarize(data, base_url="http://127.0.0.1:52415", expected_nodes=4)
    assert exo.topology_nodes == 1
    assert exo.mesh_ok is False
    assert "WARN" in exo.summary
