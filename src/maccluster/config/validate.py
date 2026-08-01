"""Pure config validation."""

from __future__ import annotations

from maccluster.constants import (
    MAX_NODES,
    MIN_HEAL_INTERVAL_S,
    MIN_NODES,
    SUPPORTED_SCHEMA_VERSIONS,
)
from maccluster.domain.enums import NodeRole
from maccluster.domain.invariants import config_basic_ok
from maccluster.domain.models import ClusterConfig, HostIdentity, Node
from maccluster.errors import ConfigError
from maccluster.platform.identity import match_self


def validate_config(cfg: ClusterConfig) -> list[str]:
    """Return list of validation errors (empty if valid). Does not check self-match."""
    errors: list[str] = []
    if cfg.schema_version not in SUPPORTED_SCHEMA_VERSIONS:
        errors.append(
            f"unsupported schema_version {cfg.schema_version} "
            f"(supported: {sorted(SUPPORTED_SCHEMA_VERSIONS)})"
        )
    if not (MIN_NODES <= len(cfg.nodes) <= MAX_NODES):
        if len(cfg.nodes) < MIN_NODES:
            errors.append(f"2–4 nodes required (got {len(cfg.nodes)})")
        else:
            errors.append(f"max 4 nodes in v1 (got {len(cfg.nodes)})")
    if cfg.heal_interval_seconds < MIN_HEAL_INTERVAL_S:
        errors.append(
            f"heal_interval_seconds must be >= {MIN_HEAL_INTERVAL_S} "
            f"(got {cfg.heal_interval_seconds})"
        )
    errors.extend(config_basic_ok(cfg))
    # Deduplicate while preserving order
    seen: set[str] = set()
    out: list[str] = []
    for e in errors:
        if e not in seen:
            seen.add(e)
            out.append(e)
    return out


def validate_or_raise(cfg: ClusterConfig) -> None:
    errors = validate_config(cfg)
    if errors:
        raise ConfigError("; ".join(errors), details=errors)


def assign_roles(cfg: ClusterConfig, identity: HostIdentity) -> tuple[ClusterConfig, Node]:
    """Validate, match self, return config with roles and the self node."""
    validate_or_raise(cfg)
    self_node = match_self(cfg.nodes, identity)
    nodes = tuple(
        n.with_role(NodeRole.SELF if n.id == self_node.id else NodeRole.PEER) for n in cfg.nodes
    )
    new_cfg = ClusterConfig(
        schema_version=cfg.schema_version,
        name=cfg.name,
        subnet=cfg.subnet,
        bridge_interface=cfg.bridge_interface,
        nodes=nodes,
        heal_interval_seconds=cfg.heal_interval_seconds,
        ssh_probes_enabled=cfg.ssh_probes_enabled,
        created_at=cfg.created_at,
        updated_at=cfg.updated_at,
    )
    self_with_role = self_node.with_role(NodeRole.SELF)
    return new_cfg, self_with_role
