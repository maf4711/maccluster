"""Load TOML text into ClusterConfig (pure given text)."""

from __future__ import annotations

import tomllib
from ipaddress import IPv4Address, IPv4Network
from typing import Any

from maccluster.constants import (
    DEFAULT_BRIDGE,
    DEFAULT_HEAL_INTERVAL_S,
    SCHEMA_VERSION,
)
from maccluster.domain.models import (
    DEFAULT_TRANSPORT_PRIORITY,
    TRANSPORT_NAMES,
    ClusterConfig,
    Node,
)
from maccluster.errors import ConfigError


def load_toml_text(text: str) -> ClusterConfig:
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"TOML syntax error: {exc}") from exc
    return load_dict(data)


def load_dict(data: dict[str, Any]) -> ClusterConfig:
    if "schema_version" not in data:
        raise ConfigError("missing required field: schema_version")
    try:
        schema_version = int(data["schema_version"])
    except (TypeError, ValueError) as exc:
        raise ConfigError("schema_version must be an integer") from exc

    name = str(data.get("name", "")).strip()
    if not name:
        raise ConfigError("missing or empty field: name")

    subnet_raw = data.get("subnet")
    if not subnet_raw:
        raise ConfigError("missing required field: subnet")
    try:
        subnet = IPv4Network(str(subnet_raw), strict=False)
    except ValueError as exc:
        raise ConfigError(f"invalid subnet: {subnet_raw!r}") from exc

    bridge = str(data.get("bridge_interface", DEFAULT_BRIDGE)).strip()
    heal = int(data.get("heal_interval_seconds", DEFAULT_HEAL_INTERVAL_S))
    ssh = bool(data.get("ssh_probes_enabled", False))
    transport_priority = _parse_transport_priority(data.get("transport_priority"))

    nodes_raw = data.get("nodes")
    if not isinstance(nodes_raw, list):
        raise ConfigError("missing or invalid field: nodes (expected array of tables)")

    nodes: list[Node] = []
    for i, raw in enumerate(nodes_raw):
        if not isinstance(raw, dict):
            raise ConfigError(f"nodes[{i}] must be a table")
        nodes.append(_parse_node(raw, i))

    return ClusterConfig(
        schema_version=schema_version if schema_version else SCHEMA_VERSION,
        name=name,
        subnet=subnet,
        bridge_interface=bridge,
        nodes=tuple(nodes),
        heal_interval_seconds=heal,
        ssh_probes_enabled=ssh,
        transport_priority=transport_priority,
    )


def _parse_transport_priority(raw: Any) -> tuple[str, ...]:
    """Optional ``transport_priority = ["rdma", "tb", "wifi"]``; absent → default."""
    if raw is None:
        return DEFAULT_TRANSPORT_PRIORITY
    allowed = ", ".join(DEFAULT_TRANSPORT_PRIORITY)
    if not isinstance(raw, list) or not all(isinstance(x, str) for x in raw):
        raise ConfigError(f"transport_priority must be an array of strings (allowed: {allowed})")
    names = tuple(x.strip().lower() for x in raw)
    if not names:
        raise ConfigError(f"transport_priority must not be empty (allowed: {allowed})")
    unknown = [n for n in names if n not in TRANSPORT_NAMES]
    if unknown:
        raise ConfigError(f"transport_priority: unknown transport {unknown!r} (allowed: {allowed})")
    if len(set(names)) != len(names):
        raise ConfigError(f"transport_priority: duplicate entries in {list(names)!r}")
    return names


def _parse_node(raw: dict[str, Any], index: int) -> Node:
    nid = str(raw.get("id", "")).strip()
    if not nid:
        raise ConfigError(f"nodes[{index}]: missing id")

    hostnames_raw = raw.get("hostnames", raw.get("hostname"))
    if isinstance(hostnames_raw, str):
        hostnames = (hostnames_raw,)
    elif isinstance(hostnames_raw, list):
        hostnames = tuple(str(h).strip() for h in hostnames_raw if str(h).strip())
    else:
        hostnames = ()
    if not hostnames:
        raise ConfigError(f"nodes[{index}] ({nid}): missing hostnames")

    ip_raw = raw.get("ip")
    if not ip_raw:
        raise ConfigError(f"nodes[{index}] ({nid}): missing ip")
    try:
        ip = IPv4Address(str(ip_raw))
    except ValueError as exc:
        raise ConfigError(f"nodes[{index}] ({nid}): invalid ip {ip_raw!r}") from exc

    hw_uuid = str(raw.get("hw_uuid", "")).strip()
    if not hw_uuid:
        raise ConfigError(f"nodes[{index}] ({nid}): missing hw_uuid")

    ssh_target = raw.get("ssh_target")
    ssh_target_s = str(ssh_target).strip() if ssh_target else None

    return Node(
        id=nid,
        hostnames=hostnames,
        ip=ip,
        hw_uuid=hw_uuid,
        ssh_target=ssh_target_s,
        tb_domain_uuids=_str_list(raw.get("tb_domain_uuids")),
        tb_controller_uids=_str_list(raw.get("tb_controller_uids")),
    )


def _str_list(raw: Any) -> tuple[str, ...]:
    """Optional array of strings; blanks dropped, anything else → empty."""
    if not isinstance(raw, list):
        return ()
    return tuple(str(u).strip() for u in raw if str(u).strip())
