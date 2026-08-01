"""Build initial ClusterConfig template for `init`."""

from __future__ import annotations

from ipaddress import IPv4Address, IPv4Network

from maccluster.constants import (
    DEFAULT_BRIDGE,
    DEFAULT_HEAL_INTERVAL_S,
    DEFAULT_SUBNET,
    SCHEMA_VERSION,
)
from maccluster.domain.models import ClusterConfig, HostIdentity, Node


def build_init_config(
    identity: HostIdentity,
    *,
    name: str = "studio-cluster",
    subnet: str = DEFAULT_SUBNET,
    node_count: int = 4,
    bridge_interface: str = DEFAULT_BRIDGE,
) -> ClusterConfig:
    node_count = max(2, min(4, node_count))
    net = IPv4Network(subnet, strict=False)
    base = int(net.network_address) + 1
    nodes: list[Node] = []

    # Self as first node
    self_hosts = identity.hostnames or (identity.hostname,)
    self_id = _slug_id(identity.hostname) or "node-a"
    nodes.append(
        Node(
            id=self_id if self_id != "node" else "node-a",
            hostnames=tuple(self_hosts) if self_hosts else (identity.hostname or "localhost",),
            ip=IPv4Address(base),
            hw_uuid=identity.hw_uuid or "00000000-0000-0000-0000-000000000001",
        )
    )

    letters = "abcdefghijklmnopqrstuvwxyz"
    for i in range(1, node_count):
        letter = letters[i]
        nodes.append(
            Node(
                id=f"node-{letter}",
                hostnames=(f"mac-mini-{letter}.local", f"mac-mini-{letter}"),
                ip=IPv4Address(base + i),
                hw_uuid=f"00000000-0000-0000-0000-{i + 1:012d}",
            )
        )

    # Ensure unique ids if self_id collided with node-b pattern
    seen: set[str] = set()
    fixed: list[Node] = []
    for n in nodes:
        nid = n.id
        if nid in seen:
            nid = f"{nid}-self"
        seen.add(nid)
        fixed.append(
            Node(
                id=nid,
                hostnames=n.hostnames,
                ip=n.ip,
                hw_uuid=n.hw_uuid,
            )
        )

    return ClusterConfig(
        schema_version=SCHEMA_VERSION,
        name=name,
        subnet=net,
        bridge_interface=bridge_interface,
        nodes=tuple(fixed),
        heal_interval_seconds=DEFAULT_HEAL_INTERVAL_S,
        ssh_probes_enabled=False,
    )


def _slug_id(hostname: str) -> str:
    h = hostname.split(".")[0].lower()
    cleaned = "".join(c if c.isalnum() or c in "-_" else "-" for c in h)
    cleaned = cleaned.strip("-_") or "node-a"
    return cleaned[:64]
