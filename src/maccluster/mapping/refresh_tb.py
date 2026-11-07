"""`config refresh-tb` (pure parts): live TB ids as a cluster.toml snippet + text splice.

Nothing here touches the filesystem; the command decides whether ``--apply``
writes. Domain UUIDs change on every reboot, controller UIDs do not — the
snippet carries both so ``doctor`` can later tell "stale" from "wrong Mac".
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from maccluster.domain.enums import LinkState
from maccluster.domain.models import ClusterConfig, Node, ThunderboltSnapshot
from maccluster.mapping.peer_match import match_node
from maccluster.mapping.tb_identity import (
    check_tb_identity,
    live_controller_uids,
    live_domain_uuids,
)

__all__ = ["render_refresh_snippet", "splice_node_tb_ids"]

_ID_KEYS = ("tb_domain_uuids", "tb_controller_uids")
_TABLE_RE = re.compile(r"^\s*\[")
_NODES_RE = re.compile(r"^\s*\[\[nodes\]\]\s*(#.*)?$")


def render_refresh_snippet(
    *,
    cfg: ClusterConfig,
    self_node: Node,
    tb: ThunderboltSnapshot,
    config_path: str,
    apply: bool = False,
) -> str:
    """Comment header (what is live, verdict, peers on links) + one ``[[nodes]]`` table.

    The whole text parses as TOML; only the ``[[nodes]]`` table is meant to be
    merged into *config_path* (the rest is commentary for the operator).
    """
    mode = "apply" if apply else "dry-run: nothing written (add --apply to update the file)"
    verdict = check_tb_identity(self_node, tb)
    lines = [
        f"# maccluster config refresh-tb — {mode}",
        f"# config: {config_path}",
        f"# self: {self_node.id}  live Thunderbolt ids from {tb.source} "
        "(macOS regenerates domain UUIDs on reboot; controller UIDs are stable)",
        f"# verdict: {verdict.summary}" + (f" — {verdict.detail}" if verdict.detail else ""),
    ]
    peers = [n for n in cfg.nodes if n.id != self_node.id]
    seen: list[str] = []
    for p in tb.ports:
        row = f"#   receptacle {p.receptacle_id}: uid={p.bus_uid or '-'} domain={p.domain_uuid or '-'}"
        if p.link_state != LinkState.CONNECTED:
            row += "  idle" if p.link_state == LinkState.UNCONNECTED else "  link=?"
        else:
            m = match_node(
                nodes=peers,
                peer_uid=p.peer_uid,
                peer_domain_uuid=p.peer_domain_uuid,
                peer_hint=p.peer_name,
            )
            who = f"{m.node_id} (by {m.by})" if m else "no cluster node"
            row += (
                f"  → peer {p.peer_name or '?'} peer_domain={p.peer_domain_uuid or '-'} "
                f"peer_uid={p.peer_uid or '-'}  match: {who}"
            )
            if p.peer_domain_uuid:
                seen.append(
                    f"#   receptacle {p.receptacle_id}: {p.peer_name or '?'} "
                    f"peer_domain={p.peer_domain_uuid} → {m.node_id if m else 'unmatched'}"
                )
        lines.append(row)
    lines.append("#")
    lines.append(f"# Merge into {config_path} under the [[nodes]] block with this id:")
    lines.extend(_node_table(self_node.id, live_domain_uuids(tb), live_controller_uids(tb)))
    if seen:
        lines.append("")
        lines.append("# Peers seen on this Mac's links (their live domain UUIDs; a peer Mac does")
        lines.append("# not expose its controller UID here — run refresh-tb on that Mac for it):")
        lines.extend(seen)
    return "\n".join(lines) + "\n"


def _node_table(node_id: str, uuids: Sequence[str], uids: Sequence[str]) -> list[str]:
    out = ["[[nodes]]", f'id = "{_escape(node_id)}"']
    out.extend(_array("tb_domain_uuids", uuids))
    out.extend(_array("tb_controller_uids", uids))
    return out


def _array(key: str, values: Sequence[str]) -> list[str]:
    if not values:
        return [f"{key} = []"]
    return [f"{key} = [", *(f'  "{_escape(v)}",' for v in values), "]"]


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


# --- text-level splice (keeps every other line of cluster.toml byte-for-byte) ------------


def splice_node_tb_ids(
    text: str,
    node_id: str,
    *,
    domain_uuids: Sequence[str],
    controller_uids: Sequence[str],
) -> str:
    """Replace (or add) ``tb_domain_uuids`` / ``tb_controller_uids`` in the ``[[nodes]]``
    block whose ``id`` is *node_id*; everything else stays untouched. Idempotent.
    ``ValueError`` when no such block exists."""
    lines = text.splitlines()
    start, end = _node_block(lines, node_id)
    block = lines[start:end]
    kept = _drop_id_arrays(block)
    id_at = next(i for i, ln in enumerate(kept) if _is_id_line(ln, node_id))
    new_ids = _array("tb_domain_uuids", domain_uuids) + _array(
        "tb_controller_uids", controller_uids
    )
    merged = kept[: id_at + 1] + new_ids + kept[id_at + 1 :]
    out = lines[:start] + merged + lines[end:]
    tail = "\n" if text.endswith("\n") or not text else ""
    return "\n".join(out) + tail


def _is_id_line(line: str, node_id: str) -> bool:
    m = re.match(r'^\s*id\s*=\s*"((?:[^"\\]|\\.)*)"\s*(#.*)?$', line)
    return bool(m) and m.group(1) == node_id


def _node_block(lines: list[str], node_id: str) -> tuple[int, int]:
    headers = [i for i, ln in enumerate(lines) if _NODES_RE.match(ln)]
    for h in headers:
        end = len(lines)
        for j in range(h + 1, len(lines)):
            if _TABLE_RE.match(lines[j]):
                end = j
                break
        if any(_is_id_line(ln, node_id) for ln in lines[h:end]):
            return h, end
    raise ValueError(f"no [[nodes]] block with id = {node_id!r} in cluster.toml")


def _drop_id_arrays(block: list[str]) -> list[str]:
    """Remove existing ``tb_*`` assignments, single- or multi-line (bracket balanced)."""
    out: list[str] = []
    depth = 0
    for ln in block:
        if depth > 0:
            depth += ln.count("[") - ln.count("]")
            continue
        m = re.match(r"^\s*(\w+)\s*=", ln)
        if m and m.group(1) in _ID_KEYS:
            depth = ln.count("[") - ln.count("]")
            continue
        out.append(ln)
    return out
