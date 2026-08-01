"""Pure receptacle → interface mapping (fail-closed)."""

from __future__ import annotations

from maccluster.domain.invariants import is_valid_iface_name
from maccluster.domain.models import ThunderboltSnapshot
from maccluster.errors import ConfigError
from maccluster.mapping.layouts import DEFAULT_TB_BRIDGE, MAC_MINI_AS_LAYOUT


def resolve_target_interface(
    *,
    config_bridge: str,
    tb: ThunderboltSnapshot | None,
    available_ifaces: tuple[str, ...] | list[str] | None = None,
) -> str:
    """Resolve the interface to mutate.

    Order: validated config bridge if present in available_ifaces (or no list);
    else unique mapping from TB snapshot; else fail closed.
    """
    if not is_valid_iface_name(config_bridge):
        raise ConfigError(f"invalid bridge_interface: {config_bridge!r}")

    ifaces = list(available_ifaces) if available_ifaces is not None else None

    if ifaces is None or config_bridge in ifaces:
        # Prefer config when interface exists or we cannot list
        if ifaces is None or config_bridge in ifaces:
            return config_bridge

    # Mapping from ports
    candidates: set[str] = set()
    if tb:
        for port in tb.ports:
            if port.interface_name and is_valid_iface_name(port.interface_name):
                candidates.add(port.interface_name)
            layout_iface = MAC_MINI_AS_LAYOUT.get(str(port.receptacle_id))
            if layout_iface:
                candidates.add(layout_iface)

    if not candidates:
        candidates.add(DEFAULT_TB_BRIDGE)

    if ifaces is not None:
        candidates = {c for c in candidates if c in ifaces}

    if len(candidates) == 1:
        return next(iter(candidates))
    if config_bridge in candidates:
        return config_bridge
    if not candidates:
        raise ConfigError(
            f"cannot resolve TB interface (config {config_bridge!r} not found; "
            "mapping empty) — fail closed",
        )
    raise ConfigError(
        f"ambiguous TB interface mapping: {sorted(candidates)}; "
        f"set bridge_interface explicitly (fail closed)",
    )
