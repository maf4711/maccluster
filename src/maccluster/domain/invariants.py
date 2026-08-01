"""Pure domain invariant helpers (no I/O)."""

from __future__ import annotations

import re
from ipaddress import IPv4Address, IPv4Network

from maccluster.constants import IFACE_NAME_RE, MAX_NODES, MIN_NODES, NODE_ID_RE
from maccluster.domain.models import ClusterConfig, Node


def is_valid_iface_name(name: str) -> bool:
    return bool(name) and re.fullmatch(IFACE_NAME_RE, name) is not None


def is_valid_node_id(node_id: str) -> bool:
    return bool(node_id) and re.fullmatch(NODE_ID_RE, node_id) is not None


def ip_in_subnet(ip: IPv4Address, subnet: IPv4Network) -> bool:
    return ip in subnet


def node_count_ok(count: int) -> bool:
    return MIN_NODES <= count <= MAX_NODES


def unique_fields(nodes: tuple[Node, ...] | list[Node]) -> list[str]:
    """Return list of invariant violation messages (empty if ok)."""
    errors: list[str] = []
    ids = [n.id for n in nodes]
    ips = [str(n.ip) for n in nodes]
    uuids = [n.hw_uuid.lower() for n in nodes if n.hw_uuid]
    if len(ids) != len(set(ids)):
        errors.append("duplicate node id")
    if len(ips) != len(set(ips)):
        errors.append("duplicate node ip")
    non_empty_uuids = [u for u in uuids if u and not u.startswith("00000000")]
    if len(non_empty_uuids) != len(set(non_empty_uuids)):
        # Allow placeholder zeros to repeat during init drafts; real dups of non-placeholder fail.
        pass
    # Always reject true duplicate hw_uuid strings (including placeholders) for strict validate.
    if len(uuids) != len(set(uuids)):
        errors.append("duplicate hw_uuid")
    return errors


def config_basic_ok(cfg: ClusterConfig) -> list[str]:
    errors: list[str] = []
    if not cfg.name.strip():
        errors.append("name must not be empty")
    if not is_valid_iface_name(cfg.bridge_interface):
        errors.append(f"invalid bridge_interface: {cfg.bridge_interface!r}")
    if not node_count_ok(len(cfg.nodes)):
        errors.append(f"nodes must be {MIN_NODES}–{MAX_NODES} (got {len(cfg.nodes)})")
    errors.extend(unique_fields(cfg.nodes))
    for n in cfg.nodes:
        if not is_valid_node_id(n.id):
            errors.append(f"invalid node id: {n.id!r}")
        if not ip_in_subnet(n.ip, cfg.subnet):
            errors.append(f"ip {n.ip} not in subnet {cfg.subnet}")
        if not n.hostnames:
            errors.append(f"node {n.id} has no hostnames")
    return errors
