"""Heal loop with max_iterations."""

from __future__ import annotations

from maccluster.services.heal_loop_service import run_heal_loop


def test_loop_iterations(fake_ctx):
    # Will hit degraded each time (no TB link)
    code = run_heal_loop(fake_ctx, interval=0.01, max_iterations=2)
    assert code in (0, 3, 1)
    assert len(fake_ctx.clock.slept) >= 1
