"""Optional rich TUI for monitor (lazy import)."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from maccluster.domain.models import HealthSnapshot


def rich_available() -> bool:
    if os.environ.get("MACCLUSTER_RICH", "").strip() in ("0", "false", "no"):
        return False
    if os.environ.get("NO_COLOR", "").strip() != "":
        return False
    try:
        import rich  # noqa: F401

        return True
    except ImportError:
        return False


def render_rich_status(snap: HealthSnapshot) -> str:
    """Render with rich if available; else raise ImportError."""
    from rich.console import Console
    from rich.table import Table

    from maccluster.render.symbols import link_symbol, reachability_symbol

    console = Console(record=True, force_terminal=True, no_color=False)
    table = Table(title=f"MacCluster: {snap.cluster_name} ({snap.overall.value})")
    table.add_column("Role")
    table.add_column("ID")
    table.add_column("IP")
    table.add_column("Reach")
    table.add_column("TB Link")
    for nh in snap.nodes:
        role = "self" if snap.self_node_id == nh.node.id else "peer"
        table.add_row(
            role,
            nh.node.id,
            str(nh.node.ip),
            f"{reachability_symbol(nh.reachability)} {nh.reachability.value}",
            f"{link_symbol(nh.link_state)} {nh.link_state.value}",
        )
    console.print(table)
    return console.export_text()
