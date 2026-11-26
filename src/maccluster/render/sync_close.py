"""Closing lines of a sync report: what moved, where, and how much.

Every plain-text Abschluss (the live `sync` print and `sync --last`) ends with
this block, including a run that moved nothing.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from maccluster.domain.models import SyncHomeResult
from maccluster.render.progress import format_bytes


def _files(n: int) -> str:
    return f"{int(n)} file" if int(n) == 1 else f"{int(n)} files"


def _amount(push_files: int, push_bytes: int, pull_files: int, pull_bytes: int) -> str:
    return (
        f"push {_files(push_files)}, {format_bytes(push_bytes)}; "
        f"pull {_files(pull_files)}, {format_bytes(pull_bytes)}"
    )


def _where(peer_id: str, ssh_target: str, via: str) -> str:
    dest = (ssh_target or "").strip()
    name = (peer_id or "").strip()
    if dest and name and name not in dest:
        dest = f"{name} ({dest})"
    elif not dest:
        dest = name
    if not dest:
        dest = "(no peer)"
    if via:
        dest = f"{dest} via {via}"
    return dest


def _what_fallback(includes: Sequence[str], target: str, local_home: str) -> str:
    named = [i.strip() for i in includes if i and i.strip()]
    if named:
        return ", ".join(named)
    home = (local_home or "").strip()
    label = (target or "home").strip() or "home"
    return f"{label} ({home})" if home else label


def transfer_block_from_parts(
    *,
    peers: Sequence[Mapping[str, Any]],
    includes: Sequence[str] = (),
    local_home: str = "",
    target: str = "home",
    planned: bool = False,
) -> str:
    """Three facts per leg: what, where, how much. Always at least one leg."""
    head = "transfer: planned (nothing written)" if planned else "transfer:"
    fallback = _what_fallback(includes, target, local_home)
    lines = [head]
    legs = 0
    for peer in peers:
        via = str(peer.get("transport") or peer.get("via") or "")
        where = _where(str(peer.get("peer_id") or ""), str(peer.get("ssh_target") or ""), via)
        roots = peer.get("roots") or ()
        named = [r for r in roots if str(r.get("root") or "").strip()]
        if named:
            for root in named:
                lines.append(f"  what: {root.get('root')}")
                lines.append(f"  where: {where}")
                lines.append(
                    "  how much: "
                    + _amount(
                        int(root.get("push_files") or 0),
                        int(root.get("push_bytes") or 0),
                        int(root.get("pull_files") or 0),
                        int(root.get("pull_bytes") or 0),
                    )
                )
                legs += 1
            continue
        lines.append(f"  what: {fallback}")
        lines.append(f"  where: {where}")
        lines.append(
            "  how much: "
            + _amount(
                int(peer.get("push_files") or 0),
                int(peer.get("push_bytes") or 0),
                int(peer.get("pull_files") or 0),
                int(peer.get("pull_bytes") or 0),
            )
        )
        legs += 1
    if legs == 0:
        lines.append("  what: (none)")
        lines.append("  where: (no peer)")
        lines.append("  how much: " + _amount(0, 0, 0, 0))
    return "\n".join(lines)


def transfer_block(result: SyncHomeResult) -> str:
    peers = []
    for peer in result.peers:
        peers.append(
            {
                "peer_id": peer.peer_id,
                "ssh_target": peer.ssh_target,
                "via": peer.via,
                "transport": peer.transport,
                "push_files": peer.push_files,
                "push_bytes": peer.push_bytes,
                "pull_files": peer.pull_files,
                "pull_bytes": peer.pull_bytes,
                "roots": [
                    {
                        "root": root.root,
                        "push_files": root.push_files,
                        "push_bytes": root.push_bytes,
                        "pull_files": root.pull_files,
                        "pull_bytes": root.pull_bytes,
                    }
                    for root in peer.roots
                ],
            }
        )
    return transfer_block_from_parts(
        peers=peers,
        includes=result.includes,
        local_home=result.local_home,
        target=getattr(result, "target", "home") or "home",
        planned=bool(result.dry_run or result.compare_only),
    )


def transfer_block_from_log(data: Mapping[str, Any]) -> str:
    return transfer_block_from_parts(
        peers=list(data.get("peers") or []),
        includes=list(data.get("includes") or []),
        local_home=str(data.get("local_home") or ""),
        target=str(data.get("target") or "home"),
        planned=bool(data.get("dry_run") or data.get("compare_only")),
    )
