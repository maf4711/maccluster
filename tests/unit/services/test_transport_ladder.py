"""Transport ladder: rdma → tb → wifi ordering, filtering, override, probing."""

from __future__ import annotations

from collections.abc import Sequence
from ipaddress import IPv4Address

import pytest

from maccluster.cli.parser import build_parser
from maccluster.config.load import load_toml_text
from maccluster.doctor_logic.checks import check_rdma_device_to_peer
from maccluster.domain.enums import CheckSeverity
from maccluster.domain.models import Node, RdmaStatus
from maccluster.errors import CliError, ConfigError
from maccluster.ports.process import ProcessResult
from maccluster.services.transport_ladder import (
    DEFAULT_TRANSPORT_PRIORITY,
    TRANSPORT_NAMES,
    TransportFailed,
    TransportProbe,
    arep_peer_for_node,
    arep_status_json,
    choose_transports,
    probe_transports,
)

NODE_B = Node(
    id="node-b",
    hostnames=("mac-mini-b.local", "mac-mini-b"),
    ip=IPv4Address("10.42.0.2"),
    hw_uuid="00000000-0000-0000-0000-000000000002",
    ssh_target="mafoe@10.42.0.2",
)

# Shape of `arep status --json` after W4 (Swift Codable → camelCase keys).
AREP_STATUS = {
    "version": "0.4.0",
    "fingerprint": "SHA256:aaaa",
    "rdmaDevices": ["rdma_en2", "rdma_en3"],
    "peers": [
        {
            "displayName": "mac-mini-b",
            "fingerprint": "SHA256:bbbb",
            "trust": "trusted",
            "transportCapable": ["rdma", "tcp"],
            "lastTransport": "rdma",
            "lastDowngradeReason": None,
        },
        {
            "displayName": "mac-mini-c",
            "fingerprint": "SHA256:cccc",
            "trust": "unpaired",
            "transportCapable": ["tcp"],
        },
    ],
}


def _probe(*, rdma: bool = True, tb: bool = True, wifi: str | None = "u@b.local") -> TransportProbe:
    return TransportProbe(rdma_available=rdma, tb_reachable=tb, wifi_target=wifi, detail={})


# --- constants / errors -------------------------------------------------------


def test_default_priority_and_names():
    assert DEFAULT_TRANSPORT_PRIORITY == ("rdma", "tb", "wifi")
    assert TRANSPORT_NAMES == frozenset({"rdma", "tb", "wifi"})


def test_transport_failed_carries_transport_and_reason():
    exc = TransportFailed("rdma", "link lost")
    assert exc.transport == "rdma"
    assert exc.reason == "link lost"
    assert "rdma" in str(exc) and "link lost" in str(exc)
    assert isinstance(exc, CliError)


# --- choose_transports ------------------------------------------------------------


def test_choose_all_available_keeps_configured_order():
    assert choose_transports(_probe(), DEFAULT_TRANSPORT_PRIORITY) == ["rdma", "tb", "wifi"]
    assert choose_transports(_probe(), ("wifi", "rdma", "tb")) == ["wifi", "rdma", "tb"]


def test_choose_filters_unavailable_rungs():
    assert choose_transports(_probe(rdma=False), DEFAULT_TRANSPORT_PRIORITY) == ["tb", "wifi"]
    assert choose_transports(_probe(rdma=False, tb=False), DEFAULT_TRANSPORT_PRIORITY) == ["wifi"]
    assert choose_transports(_probe(wifi=None), DEFAULT_TRANSPORT_PRIORITY) == ["rdma", "tb"]
    assert choose_transports(_probe(rdma=False, tb=False, wifi=None), ("rdma", "tb", "wifi")) == []


def test_choose_priority_subset_only_lists_configured():
    assert choose_transports(_probe(), ("tb",)) == ["tb"]
    assert choose_transports(_probe(), ("tb", "rdma")) == ["tb", "rdma"]


def test_choose_override_available_returns_only_that_one():
    assert choose_transports(_probe(), DEFAULT_TRANSPORT_PRIORITY, override="tb") == ["tb"]
    # Override wins even when the rung is not in the configured priority.
    assert choose_transports(_probe(), ("tb",), override="wifi") == ["wifi"]


def test_choose_override_unavailable_raises_transport_failed():
    with pytest.raises(TransportFailed) as ei:
        choose_transports(_probe(rdma=False), DEFAULT_TRANSPORT_PRIORITY, override="rdma")
    assert ei.value.transport == "rdma"
    assert "unavailable" in ei.value.reason
    with pytest.raises(TransportFailed) as ei2:
        choose_transports(_probe(wifi=None), DEFAULT_TRANSPORT_PRIORITY, override="wifi")
    assert ei2.value.transport == "wifi"


def test_choose_rejects_unknown_names():
    with pytest.raises(ValueError):
        choose_transports(_probe(), ("rdma", "usb"))
    with pytest.raises(ValueError):
        choose_transports(_probe(), DEFAULT_TRANSPORT_PRIORITY, override="usb")


# --- arep status parsing ------------------------------------------------------------


def test_arep_peer_for_node_matches_hostname_ignoring_local_suffix_and_case():
    peer = arep_peer_for_node(AREP_STATUS, NODE_B)
    assert peer is not None
    assert peer["fingerprint"] == "SHA256:bbbb"
    upper = {"peers": [{"displayName": "MAC-MINI-B.local", "trust": "trusted"}]}
    assert arep_peer_for_node(upper, NODE_B) is not None


def test_arep_peer_for_node_matches_fingerprint_or_id():
    by_fp = {"peers": [{"displayName": "other", "fingerprint": "node-b", "trust": "trusted"}]}
    assert arep_peer_for_node(by_fp, NODE_B) is not None
    by_id = {"peers": [{"displayName": "node-b", "trust": "trusted"}]}
    assert arep_peer_for_node(by_id, NODE_B) is not None


def test_arep_peer_for_node_none_on_missing_or_malformed():
    assert arep_peer_for_node(None, NODE_B) is None
    assert arep_peer_for_node({}, NODE_B) is None
    assert arep_peer_for_node({"peers": "nope"}, NODE_B) is None
    assert arep_peer_for_node({"peers": [None, 3, {"displayName": "zzz"}]}, NODE_B) is None


def test_probe_rdma_available_when_trusted_and_rdma_capable(fake_ctx):
    probe = probe_transports(
        NODE_B,
        fake_ctx,
        arep_status=lambda: AREP_STATUS,
        tb_ping=lambda ip: ip == "10.42.0.2",
        wifi_target=lambda n: "mafoe@mac-mini-b.local",
    )
    assert probe.rdma_available is True
    assert probe.tb_reachable is True
    assert probe.wifi_target == "mafoe@mac-mini-b.local"
    assert probe.detail["arep_peer"] == "mac-mini-b"
    assert probe.detail["arep_trust"] == "trusted"
    assert probe.available() == ("rdma", "tb", "wifi")


def test_probe_rdma_unavailable_when_not_trusted_or_not_capable(fake_ctx):
    not_trusted = {
        "peers": [{"displayName": "mac-mini-b", "trust": "unpaired", "transportCapable": ["rdma"]}]
    }
    p1 = probe_transports(
        NODE_B,
        fake_ctx,
        arep_status=lambda: not_trusted,
        tb_ping=lambda ip: False,
        wifi_target=lambda n: None,
    )
    assert p1.rdma_available is False
    assert p1.tb_reachable is False
    assert p1.wifi_target is None
    assert p1.available() == ()
    tcp_only = {
        "peers": [{"displayName": "mac-mini-b", "trust": "trusted", "transportCapable": ["tcp"]}]
    }
    p2 = probe_transports(
        NODE_B,
        fake_ctx,
        arep_status=lambda: tcp_only,
        tb_ping=lambda ip: True,
        wifi_target=lambda n: None,
    )
    assert p2.rdma_available is False
    assert p2.detail["rdma_reason"]


def test_probe_rdma_unavailable_when_arep_status_missing_or_raises(fake_ctx):
    p = probe_transports(
        NODE_B,
        fake_ctx,
        arep_status=lambda: None,
        tb_ping=lambda ip: True,
        wifi_target=lambda n: "u@h.local",
    )
    assert p.rdma_available is False
    assert "arep" in p.detail["rdma_reason"]

    def boom() -> dict | None:
        raise OSError("no arep")

    p2 = probe_transports(
        NODE_B, fake_ctx, arep_status=boom, tb_ping=lambda ip: True, wifi_target=lambda n: None
    )
    assert p2.rdma_available is False
    assert p2.tb_reachable is True


def test_probe_tb_reachable_via_ssh_port_when_icmp_is_filtered(fake_ctx):
    # node-b drops ICMP (memory note) but answers SSH on the TB IP. The default
    # tb probe must use a TCP connect to port 22, not ping, or the tb rung is
    # skipped even though ssh (and the whole remote inventory) works (sync F6).
    from maccluster.domain.enums import ReachabilityState

    fake_ctx.reachability.states["10.42.0.2"] = ReachabilityState.DOWN  # ICMP filtered
    fake_ctx.reachability.tcp_states["10.42.0.2"] = ReachabilityState.UP  # SSH answers
    p = probe_transports(
        NODE_B,
        fake_ctx,
        arep_status=lambda: AREP_STATUS,
        wifi_target=lambda n: "u@b.local",
    )
    assert p.tb_reachable is True


def test_probe_tb_unreachable_when_neither_icmp_nor_ssh_answers(fake_ctx):
    from maccluster.domain.enums import ReachabilityState

    fake_ctx.reachability.states["10.42.0.2"] = ReachabilityState.DOWN
    fake_ctx.reachability.tcp_states["10.42.0.2"] = ReachabilityState.DOWN
    p = probe_transports(NODE_B, fake_ctx, arep_status=lambda: AREP_STATUS, wifi_target=lambda n: None)
    assert p.tb_reachable is False


def test_probe_swallows_ping_and_wifi_errors(fake_ctx):
    def bad_ping(ip: str) -> bool:
        raise RuntimeError("ping exploded")

    def bad_wifi(n: Node) -> str | None:
        raise RuntimeError("no user")

    p = probe_transports(
        NODE_B, fake_ctx, arep_status=lambda: AREP_STATUS, tb_ping=bad_ping, wifi_target=bad_wifi
    )
    assert p.rdma_available is True
    assert p.tb_reachable is False
    assert p.wifi_target is None


# --- arep_status_json via process adapter ----------------------------------------


class _FakeRunner:
    def __init__(self, result: ProcessResult | Exception) -> None:
        self.result = result
        self.calls: list[tuple[str, ...]] = []

    def resolve(self, basename: str) -> str:
        return f"/Users/x/.local/bin/{basename}"

    def run(self, argv: Sequence[str], *, timeout: float, check: bool = False) -> ProcessResult:
        self.calls.append(tuple(argv))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def _ok(stdout: str, rc: int = 0) -> ProcessResult:
    return ProcessResult(argv=("arep", "status", "--json"), returncode=rc, stdout=stdout, stderr="")


def test_arep_status_json_parses_dict_and_passes_argv():
    runner = _FakeRunner(_ok('{"peers": [], "rdmaDevices": ["rdma_en2"]}\n'))
    out = arep_status_json(runner=runner)
    assert out == {"peers": [], "rdmaDevices": ["rdma_en2"]}
    assert runner.calls == [("arep", "status", "--json")]


def test_arep_status_json_uses_explicit_binary():
    runner = _FakeRunner(_ok("{}"))
    assert arep_status_json("/opt/arep/bin/arep", runner=runner) == {}
    assert runner.calls[0][0] == "/opt/arep/bin/arep"


@pytest.mark.parametrize(
    "result",
    [
        _ok("", rc=1),
        _ok("not json"),
        _ok("[1, 2]"),
        _ok('"str"'),
        ProcessResult(argv=("arep",), returncode=124, stdout="{}", stderr="", timed_out=True),
        CliError("tool not found: arep", exit_code=1),
        OSError("spawn failed"),
    ],
)
def test_arep_status_json_none_on_any_failure(result):
    assert arep_status_json(runner=_FakeRunner(result)) is None


def test_arep_status_json_default_runner_allows_arep_basename():
    # The project allowlist does not know `arep`; the ladder's own runner must.
    from maccluster.services.transport_ladder import arep_process_runner

    runner = arep_process_runner()
    try:
        path = runner.resolve("arep")
        assert path.endswith("/arep")
    except CliError as exc:
        assert "not found" in exc.message
        assert "allowlisted" not in exc.message
    with pytest.raises(CliError):
        runner.resolve("curl")


# --- config: transport_priority ---------------------------------------------------


def test_config_default_priority_when_key_absent(valid_4_toml: str):
    cfg = load_toml_text(valid_4_toml)
    assert cfg.schema_version == 1
    assert cfg.transport_priority == DEFAULT_TRANSPORT_PRIORITY


def test_config_reads_custom_priority(valid_4_toml: str):
    cfg = load_toml_text('transport_priority = ["tb", "wifi"]\n' + valid_4_toml)
    assert cfg.transport_priority == ("tb", "wifi")


def test_config_rejects_unknown_transport_name(valid_4_toml: str):
    with pytest.raises(ConfigError) as ei:
        load_toml_text('transport_priority = ["rdma", "usb"]\n' + valid_4_toml)
    msg = ei.value.message
    assert "transport_priority" in msg and "usb" in msg and "rdma" in msg


@pytest.mark.parametrize(
    "raw",
    ['transport_priority = "rdma"', "transport_priority = []", 'transport_priority = ["tb", "tb"]'],
)
def test_config_rejects_malformed_priority(valid_4_toml: str, raw: str):
    with pytest.raises(ConfigError) as ei:
        load_toml_text(raw + "\n" + valid_4_toml)
    assert "transport_priority" in ei.value.message


def test_config_priority_survives_assign_roles_and_dump(valid_4_toml: str):
    from maccluster.config.dump import dump_toml
    from maccluster.config.validate import assign_roles
    from maccluster.domain.models import HostIdentity

    cfg = load_toml_text('transport_priority = ["tb"]\n' + valid_4_toml)
    ident = HostIdentity(
        hostname="mac-mini-a",
        hostnames=("mac-mini-a",),
        hw_uuid="00000000-0000-0000-0000-000000000001",
    )
    bound, _self = assign_roles(cfg, ident)
    assert bound.transport_priority == ("tb",)
    again = load_toml_text(dump_toml(cfg))
    assert again.transport_priority == ("tb",)
    # Default stays implicit so existing dumps do not change.
    assert "transport_priority" not in dump_toml(load_toml_text(valid_4_toml))


# --- parser: --transport ----------------------------------------------------------


@pytest.mark.parametrize("target", ["home", "dev"])
def test_parser_transport_flag(target: str):
    parser = build_parser()
    for name in ("rdma", "tb", "wifi"):
        args = parser.parse_args(["sync", target, "--transport", name])
        assert args.transport == name
    assert parser.parse_args(["sync", target]).transport is None
    with pytest.raises(SystemExit):
        parser.parse_args(["sync", target, "--transport", "usb"])


def test_parser_transport_mutex_with_wifi_flags_on_dev():
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["sync", "dev", "--transport", "rdma", "--wifi-only"])
    with pytest.raises(SystemExit):
        parser.parse_args(["sync", "dev", "--transport", "tb", "--no-wifi"])


# --- doctor: rdma_no_device_to_peer -------------------------------------------------


def test_check_rdma_device_to_peer_warns_when_enabled_but_no_capable_peer():
    rdma = RdmaStatus(tool_available=True, enabled=True)
    peers = [{"displayName": "b", "trust": "trusted", "transportCapable": ["tcp"]}]
    f = check_rdma_device_to_peer(rdma, peers)
    assert f.check_id == "rdma_no_device_to_peer"
    assert f.severity == CheckSeverity.WARN
    f2 = check_rdma_device_to_peer(rdma, [])
    assert f2.severity == CheckSeverity.WARN


def test_check_rdma_device_to_peer_info_when_capable_peer_or_rdma_off():
    rdma = RdmaStatus(tool_available=True, enabled=True)
    peers = [{"displayName": "b", "trust": "trusted", "transportCapable": ["rdma", "tcp"]}]
    f = check_rdma_device_to_peer(rdma, peers)
    assert f.severity == CheckSeverity.INFO
    assert "b" in f.summary
    off = check_rdma_device_to_peer(RdmaStatus(tool_available=True, enabled=False), [])
    assert off.severity == CheckSeverity.INFO
    unknown = check_rdma_device_to_peer(RdmaStatus(tool_available=False, enabled=None), [])
    assert unknown.severity == CheckSeverity.INFO
    assert check_rdma_device_to_peer(None, []).severity == CheckSeverity.INFO
