"""Fleet SSH hops stay on the TB subnet."""

from __future__ import annotations

from ipaddress import IPv4Address

import pytest

from maccluster.domain.enums import NodeRole
from maccluster.domain.models import Node
from maccluster.errors import CliError
from maccluster.ports.process import ProcessResult
from maccluster.services.fleet_exec import directed_pairs, iter_peers, run_on_peer


def _node(nid: str, ip: str, *, role: NodeRole = NodeRole.PEER) -> Node:
    return Node(
        id=nid,
        hostnames=(nid,),
        ip=IPv4Address(ip),
        hw_uuid="0" * 32,
        role=role,
    )


class RecordingRunner:
    def __init__(self, *, rc: int = 0, stdout: str = "ok") -> None:
        self.calls: list[tuple[str, ...]] = []
        self.rc = rc
        self.stdout = stdout

    def resolve(self, basename: str) -> str:
        if basename == "ssh":
            return "/usr/bin/ssh"
        raise CliError(f"tool not found: {basename}", exit_code=1)

    def run(self, argv, *, timeout: float = 15.0, check: bool = False) -> ProcessResult:
        full = tuple(str(a) for a in argv)
        self.calls.append(full)
        return ProcessResult(
            argv=full,
            returncode=self.rc,
            stdout=self.stdout,
            stderr="" if self.rc == 0 else "fail",
        )


class Ctx:
    def __init__(self, runner) -> None:
        self.runner = runner


def test_iter_peers_skips_self_and_filters():
    self_n = _node("node-a", "10.42.0.1", role=NodeRole.SELF)
    b = _node("node-b", "10.42.0.2")
    c = _node("node-c", "10.42.0.3")

    class Cfg:
        nodes = (self_n, b, c)

    peers = iter_peers(Cfg(), self_n)
    assert [p.id for p in peers] == ["node-b", "node-c"]
    only_b = iter_peers(Cfg(), self_n, peer="10.42.0.2")
    assert [p.id for p in only_b] == ["node-b"]
    with pytest.raises(CliError) as ei:
        iter_peers(Cfg(), self_n, peer="missing")
    assert ei.value.exit_code == 2


def test_directed_pairs_local_only_vs_full_mesh():
    self_n = _node("node-a", "10.42.0.1", role=NodeRole.SELF)
    b = _node("node-b", "10.42.0.2")
    local = directed_pairs(self_n, (b,), orchestrated=False)
    assert [(s.id, d.id) for s, d in local] == [("node-a", "node-b")]
    full = directed_pairs(self_n, (b,), orchestrated=True)
    assert [(s.id, d.id) for s, d in full] == [
        ("node-a", "node-b"),
        ("node-b", "node-a"),
    ]


def test_run_on_peer_binds_self_ip_and_refuses_lan():
    runner = RecordingRunner()
    hop = run_on_peer(
        Ctx(runner),
        self_ip="10.42.0.1",
        node=_node("node-b", "10.42.0.2"),
        remote=("true",),
        timeout=5.0,
        connect_timeout=2,
        user="a321",
    )
    assert hop.ok
    assert hop.node_id == "node-b"
    assert hop.peer_ip == "10.42.0.2"
    joined = " ".join(runner.calls[0])
    assert "BindAddress=10.42.0.1" in joined
    assert "a321@10.42.0.2" in joined
    assert joined.endswith("true")

    with pytest.raises(CliError) as ei:
        run_on_peer(
            Ctx(runner),
            self_ip="10.42.0.1",
            node=_node("wifi", "192.168.1.9"),
            remote=("true",),
            timeout=5.0,
        )
    assert ei.value.exit_code == 2
