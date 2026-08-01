"""Symbols always textual."""

from __future__ import annotations

from maccluster.domain.enums import LinkState, ReachabilityState
from maccluster.render.symbols import link_symbol, reachability_symbol


def test_symbols_distinct():
    assert reachability_symbol(ReachabilityState.UP) != reachability_symbol(ReachabilityState.DOWN)
    assert link_symbol(LinkState.CONNECTED) != link_symbol(LinkState.UNCONNECTED)
    assert "[" in reachability_symbol(ReachabilityState.UP)
