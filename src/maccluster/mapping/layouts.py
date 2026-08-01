"""Known Mac mini Thunderbolt layout tables."""

from __future__ import annotations

# receptacle_id -> preferred interface name (best-effort documentation tables)
MAC_MINI_AS_LAYOUT: dict[str, str] = {
    "1": "bridge0",
    "2": "bridge0",
    "3": "bridge0",
}

DEFAULT_TB_BRIDGE = "bridge0"
