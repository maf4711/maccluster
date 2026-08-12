"""Fabric mesh health: peer reachability matrix (alive ≠ fully meshed)."""

from __future__ import annotations

from maccluster.domain.enums import LinkState, MeshVerdict, NodeRole, ReachabilityState
from maccluster.domain.models import BridgeInterface, MeshHealth, NodeHealth, ThunderboltSnapshot


def build_mesh_health(
    nodes: list[NodeHealth] | tuple[NodeHealth, ...],
    *,
    self_node_id: str | None,
    bridge: BridgeInterface | None = None,
    tb: ThunderboltSnapshot | None = None,
) -> MeshHealth:
    peers = [
        nh
        for nh in nodes
        if not ((self_node_id and nh.node.id == self_node_id) or nh.node.role == NodeRole.SELF)
    ]
    expected = len(peers)
    up = sum(1 for nh in peers if nh.reachability == ReachabilityState.UP)
    down = sum(1 for nh in peers if nh.reachability == ReachabilityState.DOWN)
    unknown = expected - up - down

    bridge_ok = bool(bridge and bridge.exists and bridge.admin_up and bool(bridge.addresses))
    tb_links = 0
    if tb and tb.ports:
        tb_links = sum(1 for p in tb.ports if p.link_state == LinkState.CONNECTED)

    if expected == 0:
        verdict = MeshVerdict.SINGLE
        summary = "single-node config (no peers)"
    elif up == expected and down == 0 and unknown == 0:
        verdict = MeshVerdict.OK
        summary = f"ok {up}/{expected} peers up"
    elif up == 0:
        verdict = MeshVerdict.ISOLATED
        summary = f"isolated 0/{expected} peers up (self alive, fabric not meshed)"
    else:
        verdict = MeshVerdict.PARTIAL
        summary = f"partial {up}/{expected} peers up ({down} down, {unknown} unknown)"

    # Annotate fabric self-alive vs mesh
    alive_bits: list[str] = []
    if bridge_ok:
        alive_bits.append("bridge")
    if tb_links:
        alive_bits.append(f"tb_links={tb_links}")
    if alive_bits and verdict != MeshVerdict.OK and expected > 0:
        summary = f"{summary}; self-alive via {','.join(alive_bits)}"

    return MeshHealth(
        expected_peers=expected,
        peers_up=up,
        peers_down=down,
        peers_unknown=unknown,
        fully_meshed=verdict == MeshVerdict.OK,
        verdict=verdict,
        summary=summary,
        bridge_ok=bridge_ok,
        tb_links=tb_links,
    )
