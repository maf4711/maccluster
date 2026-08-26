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

    from maccluster.health.traffic import format_bps, format_pps
    from maccluster.render.symbols import link_symbol, reachability_symbol

    console = Console(record=True, force_terminal=True, no_color=False)
    table = Table(title=f"MacCluster: {snap.cluster_name} ({snap.overall.value})")
    table.add_column("Role")
    table.add_column("ID")
    table.add_column("IP")
    table.add_column("Reach")
    table.add_column("TB Link")
    table.add_column("RTT")
    for nh in snap.nodes:
        role = "self" if snap.self_node_id == nh.node.id else "peer"
        rtt = f"{nh.rtt_ms:.1f}ms" if nh.rtt_ms is not None else "-"
        table.add_row(
            role,
            nh.node.id,
            str(nh.node.ip),
            f"{reachability_symbol(nh.reachability)} {nh.reachability.value}",
            f"{link_symbol(nh.link_state)} {nh.link_state.value}",
            rtt,
        )
    console.print(table)

    if snap.traffic:
        traf = Table(title="Traffic (live rates)")
        traf.add_column("Iface")
        traf.add_column("RX")
        traf.add_column("TX")
        traf.add_column("RX pps")
        traf.add_column("TX pps")
        traf.add_column("Err in/out")
        traf.add_column("Δt")
        for t in snap.traffic:
            if t.rate_available:
                err = f"{t.ierrs}/{t.oerrs} (+{t.ierrs_delta or 0}/+{t.oerrs_delta or 0})"
                dt = f"{t.sample_dt_s:.1f}s" if t.sample_dt_s is not None else "-"
                traf.add_row(
                    t.name,
                    format_bps(t.rx_bps),
                    format_bps(t.tx_bps),
                    format_pps(t.rx_pps),
                    format_pps(t.tx_pps),
                    err,
                    dt,
                )
            else:
                traf.add_row(
                    t.name,
                    f"{t.ibytes} B",
                    f"{t.obytes} B",
                    str(t.ipkts),
                    str(t.opkts),
                    f"{t.ierrs}/{t.oerrs}",
                    "n/a",
                )
        console.print(traf)

    return console.export_text()
