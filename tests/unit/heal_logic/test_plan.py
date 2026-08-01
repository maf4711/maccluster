"""Heal plan pure tests."""

from __future__ import annotations

from ipaddress import IPv4Address

from maccluster.domain.enums import HealActionKind
from maccluster.domain.models import BridgeInterface
from maccluster.heal_logic.idempotency import is_noop
from maccluster.heal_logic.plan import plan_ensure


def test_already_configured():
    obs = BridgeInterface(
        name="bridge0",
        exists=True,
        admin_up=True,
        addresses=(IPv4Address("10.42.0.1"),),
    )
    actions = plan_ensure(
        interface="bridge0",
        desired_ip=IPv4Address("10.42.0.1"),
        observed=obs,
    )
    assert is_noop(actions)
    assert actions[0].kind == HealActionKind.ALREADY_CONFIGURED


def test_needs_ip():
    obs = BridgeInterface(name="bridge0", exists=True, admin_up=True, addresses=())
    actions = plan_ensure(
        interface="bridge0",
        desired_ip=IPv4Address("10.42.0.1"),
        observed=obs,
    )
    assert any(a.kind == HealActionKind.SET_IP for a in actions)
    assert not is_noop(actions)


def test_missing_bridge():
    obs = BridgeInterface(name="bridge0", exists=False, admin_up=False)
    actions = plan_ensure(
        interface="bridge0",
        desired_ip=IPv4Address("10.42.0.1"),
        observed=obs,
    )
    kinds = {a.kind for a in actions}
    assert HealActionKind.ENSURE_BRIDGE in kinds
    assert HealActionKind.SET_IP in kinds
