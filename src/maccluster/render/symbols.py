"""Plaintext status symbols (never color-only meaning)."""

from __future__ import annotations

from maccluster.domain.enums import LinkState, ReachabilityState

# ASCII-safe defaults; rich may use others but meaning stays in text
SYM_UP = "[UP]"
SYM_DOWN = "[DOWN]"
SYM_UNKNOWN = "[??]"
SYM_SELF = "*"
SYM_PEER = " "
SYM_LINK = "[LINK]"
SYM_NOLINK = "[NO-LINK]"


def reachability_symbol(state: ReachabilityState) -> str:
    if state == ReachabilityState.UP:
        return SYM_UP
    if state == ReachabilityState.DOWN:
        return SYM_DOWN
    return SYM_UNKNOWN


def link_symbol(state: LinkState) -> str:
    if state == LinkState.CONNECTED:
        return SYM_LINK
    if state == LinkState.UNCONNECTED:
        return SYM_NOLINK
    return SYM_UNKNOWN
