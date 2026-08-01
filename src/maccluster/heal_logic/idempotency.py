"""Idempotency helpers for heal actions."""

from __future__ import annotations

from maccluster.domain.enums import HealActionKind
from maccluster.domain.models import HealAction


def is_noop(actions: list[HealAction] | tuple[HealAction, ...]) -> bool:
    if not actions:
        return True
    return all(a.kind in (HealActionKind.NOOP, HealActionKind.ALREADY_CONFIGURED) for a in actions)


def summarize(actions: list[HealAction] | tuple[HealAction, ...]) -> str:
    if is_noop(actions):
        return "already configured"
    kinds = ", ".join(a.kind.value for a in actions)
    return f"apply: {kinds}"
