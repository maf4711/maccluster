"""Serialize ClusterConfig to TOML text (handwritten template)."""

from __future__ import annotations

from maccluster.domain.models import DEFAULT_TRANSPORT_PRIORITY, ClusterConfig, Node


def dump_toml(cfg: ClusterConfig) -> str:
    lines: list[str] = [
        f"schema_version = {int(cfg.schema_version)}",
        f'name = "{_escape(cfg.name)}"',
        f'subnet = "{cfg.subnet.with_prefixlen}"',
        f'bridge_interface = "{_escape(cfg.bridge_interface)}"',
        f"heal_interval_seconds = {int(cfg.heal_interval_seconds)}",
        f"ssh_probes_enabled = {'true' if cfg.ssh_probes_enabled else 'false'}",
    ]
    if tuple(cfg.transport_priority) != DEFAULT_TRANSPORT_PRIORITY:
        rungs = ", ".join(f'"{_escape(r)}"' for r in cfg.transport_priority)
        lines.append(f"transport_priority = [{rungs}]")
    lines.append("")
    for node in cfg.nodes:
        lines.extend(_dump_node(node))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _dump_node(node: Node) -> list[str]:
    hosts = ", ".join(f'"{_escape(h)}"' for h in node.hostnames)
    lines = [
        "[[nodes]]",
        f'id = "{_escape(node.id)}"',
        f"hostnames = [{hosts}]",
        f'ip = "{node.ip}"',
        f'hw_uuid = "{_escape(node.hw_uuid)}"',
    ]
    if node.ssh_target:
        lines.append(f'ssh_target = "{_escape(node.ssh_target)}"')
    return lines


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')
