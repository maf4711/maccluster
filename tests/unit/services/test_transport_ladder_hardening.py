"""transport_ladder hardening: malformed arep status, allowlist scope, reason hygiene."""

from __future__ import annotations

from collections.abc import Sequence
from ipaddress import IPv4Address

import pytest

from maccluster.domain.models import Node
from maccluster.ports.process import ProcessResult
from maccluster.services.transport_ladder import (
    TransportFailed,
    arep_status_json,
    clean_text,
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


def test_probe_reasons_are_sanitized(fake_ctx):
    status = {
        "peers": [
            {
                "displayName": "mac-mini-b",
                "trust": "\x1b[31munpaired\x1b[0m" + "z" * 1000,
                "transportCapable": ["tcp"],
            }
        ]
    }
    probe = probe_transports(
        NODE_B,
        fake_ctx,
        arep_status=lambda: status,
        tb_ping=lambda ip: True,
        wifi_target=lambda n: None,
    )
    reason = probe.reason("rdma")
    assert "\x1b" not in reason
    assert "unpaired" in reason
    assert len(reason) <= 200
    assert "\x1b" not in probe.detail["arep_trust"]


# --- clean_text / TransportFailed ----------------------------------------------------------


def test_clean_text_strips_control_chars_and_caps():
    assert clean_text("a\x1b[31mb\x00c\nd\te", 100) == "a [31mb c d e"
    assert clean_text("x" * 500, 40) == "x" * 40
    assert clean_text(None, 10) == ""
    assert clean_text("ünï", 10) == "ünï"
    assert clean_text(["rdma", "tcp"], 40) == "['rdma', 'tcp']"


def test_transport_failed_partial_flag_and_clean_reason():
    exc = TransportFailed("rdma", "x")
    assert exc.partial is False
    assert TransportFailed("rdma", "x", partial=True).partial is True
    dirty = TransportFailed("rdma", "\x1b[31mbad\x1b[0m " + "y" * 5000)
    assert "\x1b" not in dirty.reason and "bad" in dirty.reason
    assert len(dirty.reason) <= 400
    assert "\x1b" not in str(dirty)


# --- allowlist scope: arep only, nothing wider ------------------------------------------------


def test_shared_runner_allowlist_gains_exactly_arep():
    from maccluster.adapters.process import ProcessRunner
    from maccluster.constants import ALLOWLIST_BASENAMES
    from maccluster.errors import CliError
    from maccluster.services.transport_ladder import arep_process_runner

    assert "arep" in ALLOWLIST_BASENAMES
    assert arep_process_runner()._allowlist == ALLOWLIST_BASENAMES
    for name in ("arep2", "arep ", "Arep", "sh", "python3", "curl", "arep/../sh", ""):
        with pytest.raises(CliError) as ei:
            ProcessRunner().resolve(name)
        assert "allowlisted" in ei.value.message, name


def test_shared_runner_refuses_non_arep_paths_via_prepare_argv():
    from maccluster.adapters.process import ProcessRunner
    from maccluster.errors import CliError

    runner = ProcessRunner()
    for argv in (["/tmp/x/sh", "-c", "true"], ["/tmp/x/python3"], ["/tmp/x/arep2"], []):
        with pytest.raises(CliError):
            runner._prepare_argv(argv)
    assert runner._prepare_argv(["/opt/x/arep", "status"]) == ["/opt/x/arep", "status"]
