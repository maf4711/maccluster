"""JSON status has schema_version."""

from __future__ import annotations

import json

from maccluster.render.json_out import dumps, to_jsonable
from maccluster.services.status_service import collect_status


def test_json_schema(fake_ctx):
    snap, _ = collect_status(fake_ctx)
    raw = dumps("status", to_jsonable(snap))
    data = json.loads(raw)
    assert data["schema_version"] == 1
    assert data["command"] == "status"
    assert "nodes" in data["data"]
