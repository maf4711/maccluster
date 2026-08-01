"""JSON envelope."""

from __future__ import annotations

import json

from maccluster.render.json_out import dumps, envelope


def test_envelope_schema():
    e = envelope("status", {"ok": True})
    assert e["schema_version"] == 1
    assert e["command"] == "status"
    raw = dumps("tb", {"ports": []})
    data = json.loads(raw)
    assert data["schema_version"] == 1
