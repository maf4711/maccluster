"""JSON for tb/topo."""

from __future__ import annotations

import json

from maccluster.render.json_out import dumps, to_jsonable
from maccluster.services.tb_service import probe_tb
from maccluster.services.topo_service import collect_topology


def test_tb_json(fake_ctx):
    snap = probe_tb(fake_ctx)
    data = json.loads(dumps("tb", to_jsonable(snap)))
    assert data["schema_version"] == 1
    assert data["data"]["ports"]


def test_topo_json(fake_ctx):
    topo = collect_topology(fake_ctx)
    data = json.loads(dumps("topo", to_jsonable(topo)))
    assert data["schema_version"] == 1
