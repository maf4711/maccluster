"""Pure heal plan: observed vs desired → HealAction list."""

from __future__ import annotations

from ipaddress import IPv4Address

from maccluster.domain.enums import HealActionKind
from maccluster.domain.models import BridgeInterface, HealAction


def plan_ensure(
    *,
    interface: str,
    desired_ip: IPv4Address,
    observed: BridgeInterface,
) -> list[HealAction]:
    """Build minimal actions to reach desired bridge/IP state."""
    actions: list[HealAction] = []

    if not observed.exists:
        actions.append(
            HealAction(
                kind=HealActionKind.ENSURE_BRIDGE,
                interface=interface,
                detail=f"create/ensure {interface}",
                desired_ip=desired_ip,
            )
        )
        actions.append(
            HealAction(
                kind=HealActionKind.ADMIN_UP,
                interface=interface,
                detail=f"admin up {interface}",
                desired_ip=desired_ip,
            )
        )
        actions.append(
            HealAction(
                kind=HealActionKind.SET_IP,
                interface=interface,
                detail=f"set {desired_ip} on {interface}",
                desired_ip=desired_ip,
            )
        )
        return actions

    if not observed.admin_up:
        actions.append(
            HealAction(
                kind=HealActionKind.ADMIN_UP,
                interface=interface,
                detail=f"admin up {interface}",
                desired_ip=desired_ip,
            )
        )

    if desired_ip not in observed.addresses:
        actions.append(
            HealAction(
                kind=HealActionKind.SET_IP,
                interface=interface,
                detail=f"set {desired_ip} on {interface}",
                desired_ip=desired_ip,
            )
        )

    if not actions:
        actions.append(
            HealAction(
                kind=HealActionKind.ALREADY_CONFIGURED,
                interface=interface,
                detail="already configured",
                desired_ip=desired_ip,
            )
        )
    return actions
