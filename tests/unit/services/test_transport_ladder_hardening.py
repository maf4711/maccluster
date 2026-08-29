"""transport_ladder hardening: malformed arep status, allowlist scope, reason hygiene."""

from __future__ import annotations

from collections.abc import Sequence
from ipaddress import IPv4Address

import pytest

from maccluster.domain.models import Node
from maccluster.ports.process import ProcessResult
from maccluster.services.transport_ladder import (
    arep_status_json,
    probe_transports,
)

NODE_B = Node(
    id="node-b",
    hostnames=("mac-mini-b.local", "mac-mini-b"),
    ip=IPv4Address("10.42.0.2"),
    hw_uuid="00000000-0000-0000-0000-000000000002",
    ssh_target="mafoe@10.42.0.2",
)


class _FakeRunner:
    def __init__(self, result: ProcessResult | Exception) -> None:
        self.result = result

    def resolve(self, basename: str) -> str:
        return f"/Users/x/.local/bin/{basename}"

    def run(self, argv: Sequence[str], *, timeout: float, check: bool = False) -> ProcessResult:
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


# --- probe never raises ---------------------------------------------------------------


@pytest.mark.parametrize("caps", [5, "rdma", {"rdma": True}, None, [None, 3, {"x": 1}]])
def test_probe_survives_malformed_transport_capable(fake_ctx, caps):
    status = {
        "peers": [{"displayName": "mac-mini-b", "trust": "trusted", "transportCapable": caps}]
    }
    probe = probe_transports(
        NODE_B,
        fake_ctx,
        arep_status=lambda: status,
        tb_ping=lambda ip: True,
        wifi_target=lambda n: None,
    )
    assert probe.rdma_available is False
    assert probe.detail["rdma_reason"]


def test_arep_status_json_none_on_pathological_stdout():
    deep = '{"peers":' + "[" * 100_000 + "]" * 100_000 + "}"
    out = arep_status_json(runner=_FakeRunner(ProcessResult(("arep",), 0, deep, "")))
    assert out is None or isinstance(out, dict)
    out2 = arep_status_json(runner=_FakeRunner(ProcessResult(("arep",), 0, "\x00\x01", "")))
    assert out2 is None
