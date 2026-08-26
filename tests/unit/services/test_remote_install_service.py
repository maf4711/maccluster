"""Remote install service — SSH user resolution."""

from __future__ import annotations

from ipaddress import IPv4Address

from maccluster.domain.enums import NodeRole
from maccluster.domain.models import Node
from maccluster.services.remote_install_service import resolve_install_user


def _node(ssh_target: str | None) -> Node:
    return Node(
        id="node-b",
        hostnames=("peer.local",),
        ip=IPv4Address("10.42.0.2"),
        hw_uuid="",
        role=NodeRole.PEER,
        ssh_target=ssh_target,
    )


def test_explicit_user_wins():
    assert resolve_install_user("alice", _node("mafoe@10.42.0.2")) == "alice"


def test_ssh_target_user_preferred_over_env(monkeypatch):
    monkeypatch.setenv("USER", "a321")
    assert resolve_install_user(None, _node("mafoe@10.42.0.2")) == "mafoe"


def test_ssh_target_without_user_falls_back_to_env(monkeypatch):
    monkeypatch.setenv("USER", "a321")
    assert resolve_install_user(None, _node("10.42.0.2")) == "a321"


def test_no_ssh_target_falls_back_to_env(monkeypatch):
    monkeypatch.setenv("USER", "a321")
    assert resolve_install_user(None, _node(None)) == "a321"


def test_reverse_iperf_remote_cmd_targets_self_bind():
    from maccluster.services.speedtest_service import reverse_iperf_remote_cmd

    cmd = reverse_iperf_remote_cmd("10.42.0.1", 3)
    assert "iperf3 -c 10.42.0.1 -t 3 -J" in cmd
    assert ".local/bin" in cmd  # user-local installs (no admin Homebrew)
    assert "exit 66" in cmd  # distinguishable "no iperf3 on peer"
