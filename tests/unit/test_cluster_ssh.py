"""Cluster SSH bind / subnet policy."""

from __future__ import annotations

from ipaddress import IPv4Network

import pytest

from maccluster.cluster_ssh import (
    is_cluster_ip,
    render_ssh_config_fragment,
    require_cluster_ip,
    scp_bind_argv,
    ssh_bind_argv,
)
from maccluster.errors import CliError


def test_cluster_ip_policy():
    net = IPv4Network("10.42.0.0/24")
    assert is_cluster_ip("10.42.0.2", net)
    assert not is_cluster_ip("192.168.178.127", net)
    with pytest.raises(CliError) as ei:
        require_cluster_ip("192.168.178.127", net)
    assert ei.value.exit_code == 2
    assert "bridge" in ei.value.message.lower() or "Wi" in ei.value.message


def test_ssh_bind_argv_has_bindaddress():
    argv = ssh_bind_argv(
        "/usr/bin/ssh",
        bind_ip="10.42.0.1",
        peer_ip="10.42.0.2",
        user="a321",
        remote=("/usr/bin/true",),
    )
    assert "BindAddress=10.42.0.1" in argv or "10.42.0.1" in argv
    assert "-b" in argv
    assert "a321@10.42.0.2" in argv
    assert "ControlMaster=no" in " ".join(argv) or "ControlMaster=no" in argv


def test_scp_bind_refuses_wifi():
    with pytest.raises(CliError):
        scp_bind_argv(
            "/usr/bin/scp",
            bind_ip="10.42.0.1",
            local_path="/tmp/x",
            peer_ip="192.168.1.5",
            remote_path="/tmp/x",
        )


def test_ssh_config_fragment():
    text = render_ssh_config_fragment(self_ip="10.42.0.1", user="a321")
    assert "BindAddress 10.42.0.1" in text
    assert "10.42.0.*" in text


def test_node_ssh_user_prefers_ssh_target(monkeypatch):
    from types import SimpleNamespace

    from maccluster.cluster_ssh import node_ssh_user

    monkeypatch.setenv("USER", "localuser")
    node = SimpleNamespace(ssh_target="mafoe@10.42.0.2")
    assert node_ssh_user(node) == "mafoe"
    assert node_ssh_user(node, override="alice") == "alice"
    assert node_ssh_user(SimpleNamespace(ssh_target=None)) == "localuser"
    assert node_ssh_user(SimpleNamespace(ssh_target="10.42.0.2")) == "localuser"
