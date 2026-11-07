"""`maccluster config refresh-tb`: live UUIDs/UIDs as a TOML snippet; never writes without --apply."""

from __future__ import annotations

import tomllib
from ipaddress import IPv4Address, IPv4Network
from pathlib import Path
from types import SimpleNamespace

import pytest

from maccluster.adapters.process import ProcessResult
from maccluster.cli.parser import build_parser
from maccluster.commands import config_cmd
from maccluster.domain.models import ClusterConfig, Node
from maccluster.mapping.refresh_tb import render_refresh_snippet, splice_node_tb_ids
from maccluster.mapping.tb_identity import parse_system_profiler_json

LIVE = (
    "3C9311A2-3DFC-44C4-AEC3-81086B2880BB",
    "4FBE0A88-3F0B-46F4-9511-3713B2315360",
    "DFD77E42-7ED0-48E5-A840-94FD1522F505",
)
UIDS = ("0x05AC51E771159CF0", "0x05AC51E771159CF1", "0x05AC51E771159CF2")


def _node(nid: str, ip: str, **kw) -> Node:
    return Node(
        id=nid,
        hostnames=(f"{nid}.local",),
        ip=IPv4Address(ip),
        hw_uuid=f"00000000-0000-0000-0000-00000000000{ip[-1]}",
        **kw,
    )


@pytest.fixture
def cfg() -> ClusterConfig:
    return ClusterConfig(
        schema_version=1,
        name="t",
        subnet=IPv4Network("10.42.0.0/24"),
        bridge_interface="bridge0",
        nodes=(
            _node(
                "node-a",
                "10.42.0.1",
                tb_domain_uuids=("676DF3C0-A43A-4D60-8154-6246AF7FBF00",),  # pre-reboot
                tb_controller_uids=("0x05AC51E771159CF0",),  # still this Mac
            ),
            _node("node-b", "10.42.0.2"),
            _node("node-c", "10.42.0.3", tb_domain_uuids=("BC5DEC53-7E36-4A9A-8459-456EBAB5E58A",)),
        ),
    )


@pytest.fixture
def snap(fixtures_dir: Path):
    return parse_system_profiler_json(
        (fixtures_dir / "system_profiler" / "node_a_macos27_2026-08-29.json").read_text()
    )


def test_snippet_is_valid_toml_with_live_ids_for_self(cfg, snap):
    text = render_refresh_snippet(cfg=cfg, self_node=cfg.nodes[0], tb=snap, config_path="/x")
    data = tomllib.loads(text)
    (node,) = data["nodes"]
    assert node["id"] == "node-a"
    assert tuple(node["tb_domain_uuids"]) == LIVE
    assert tuple(node["tb_controller_uids"]) == UIDS
    # dry-run banner and the stale verdict for the operator
    assert "dry-run" in text.splitlines()[0]
    assert "stale" in text  # config UUID 676DF3C0… is no longer live


def test_snippet_lists_peers_seen_on_links(cfg, snap):
    text = render_refresh_snippet(cfg=cfg, self_node=cfg.nodes[0], tb=snap, config_path="/x")
    # bus_1 peer Mac17,6 with domain BC5DEC53… matches node-c in config
    assert "receptacle 2" in text and "Mac17,6" in text
    assert "BC5DEC53-7E36-4A9A-8459-456EBAB5E58A" in text
    assert "node-c" in text
    # Studio Display is not a cluster peer; its UID still shows for completeness
    assert "0x000196C394A8D900" in text


SAMPLE_TOML = """schema_version = 1
name = "t"
subnet = "10.42.0.0/24"

[[nodes]]
id = "node-a"
tb_domain_uuids = [
  "676DF3C0-A43A-4D60-8154-6246AF7FBF00",
]
hostnames = ["a.local"]
ip = "10.42.0.1"
hw_uuid = "00000000-0000-0000-0000-000000000001"

[[nodes]]
id = "node-b"
hostnames = ["b.local"]
ip = "10.42.0.2"
hw_uuid = "00000000-0000-0000-0000-000000000002"
tb_domain_uuids = ["KEEP-ME"]
"""


def test_splice_replaces_only_the_named_node_block():
    out = splice_node_tb_ids(SAMPLE_TOML, "node-a", domain_uuids=LIVE, controller_uids=UIDS)
    data = tomllib.loads(out)
    a, b = data["nodes"]
    assert tuple(a["tb_domain_uuids"]) == LIVE
    assert tuple(a["tb_controller_uids"]) == UIDS
    assert a["hostnames"] == ["a.local"] and a["ip"] == "10.42.0.1"
    assert b["tb_domain_uuids"] == ["KEEP-ME"] and "tb_controller_uids" not in b
    assert data["name"] == "t"
    assert "676DF3C0" not in out


def test_splice_is_idempotent_and_rejects_unknown_node():
    once = splice_node_tb_ids(SAMPLE_TOML, "node-b", domain_uuids=LIVE, controller_uids=UIDS)
    twice = splice_node_tb_ids(once, "node-b", domain_uuids=LIVE, controller_uids=UIDS)
    assert once == twice
    with pytest.raises(ValueError):
        splice_node_tb_ids(SAMPLE_TOML, "node-z", domain_uuids=LIVE, controller_uids=UIDS)


def test_parser_has_config_refresh_tb():
    p = build_parser()
    a = p.parse_args(["config", "refresh-tb", "--dry-run"])
    assert a.config_action == "refresh-tb" and a.dry_run is True and a.apply is False
    b = p.parse_args(["config", "refresh-tb", "--apply"])
    assert b.apply is True


class _JsonRunner:
    """Answers ``system_profiler … -json`` with the real capture; refuses anything else."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.calls: list[list[str]] = []

    def run(self, argv, *, timeout=None, **_):
        self.calls.append(list(argv))
        if argv[:2] == ["system_profiler", "SPThunderboltDataType"] and "-json" in argv:
            return ProcessResult(argv=list(argv), returncode=0, stdout=self.text, stderr="")
        return ProcessResult(argv=list(argv), returncode=1, stdout="", stderr="denied")


def test_refresh_tb_dry_run_prints_snippet_and_never_writes(fake_ctx, fixtures_dir, capsys):
    before = fake_ctx.config_path.read_text(encoding="utf-8")
    fake_ctx.runner = _JsonRunner(
        (fixtures_dir / "system_profiler" / "node_a_macos27_2026-08-29.json").read_text()
    )
    for flags in ({"dry_run": True, "apply": False}, {"dry_run": False, "apply": False}):
        code = config_cmd.run(fake_ctx, SimpleNamespace(config_action="refresh-tb", **flags))
        out = capsys.readouterr().out
        assert code == 0
        assert "tb_controller_uids" in out and "0x05AC51E771159CF0" in out
        assert 'id = "node-a"' in out
        assert fake_ctx.config_path.read_text(encoding="utf-8") == before
    assert not list(fake_ctx.config_path.parent.glob("cluster.toml.bak*"))


def test_refresh_tb_dry_run_json(fake_ctx, fixtures_dir, capsys):
    import json

    fake_ctx.json_mode = True
    fake_ctx.runner = _JsonRunner(
        (fixtures_dir / "system_profiler" / "node_a_macos27_2026-08-29.json").read_text()
    )
    code = config_cmd.run(fake_ctx, SimpleNamespace(config_action="refresh-tb", dry_run=True))
    assert code == 0
    data = json.loads(capsys.readouterr().out)["data"]
    assert data["self"] == "node-a"
    assert data["written"] is False
    assert data["live"]["tb_controller_uids"] == list(UIDS)
    assert data["live"]["tb_domain_uuids"] == list(LIVE)
    assert "snippet" in data
