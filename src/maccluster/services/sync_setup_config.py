"""The docsync config every node gets from `maccluster sync setup` — a text edit, not a rewrite.

Every key the user set (excludes, conflict_policy, trash, skip_on_battery, …) and every comment
stays byte for byte; only the `[[peers]]` blocks are replaced (when they differ) and a missing
top-level `path` or `desktop` root is added. The llm-* roots come from `docsync roots add-llm`
on the node itself, so their allowlists are the ones of the docsync version installed there.
"""

from __future__ import annotations

import json
import re
import tomllib
from dataclasses import dataclass

DOCUMENTS = "~/Documents"
DESKTOP = ("desktop", "~/Desktop")
LLM_ROOTS = ("llm-claude", "llm-codex", "llm-gemini")
_PEERS_HEADER = re.compile(r"^\s*\[\[\s*peers\s*\]\]")
_TABLE_HEADER = re.compile(r"^\s*\[")


@dataclass(frozen=True)
class PeerEntry:
    name: str
    hosts: tuple[str, ...]
    user: str


def _toml(value) -> str:
    if isinstance(value, list | tuple):
        return "[" + ", ".join(_toml(v) for v in value) + "]"
    return json.dumps(str(value))


class PeerRename(ValueError):
    """An old peer would get a new name: its base-<root>-<peer>.json files would be orphaned."""


def _strip_peers(text: str) -> str:
    """Drop every [[peers]] block; comment and blank lines right before the next table header
    belong to that table and stay."""
    out, skipping, pending = [], False, []
    for line in text.splitlines():
        if _PEERS_HEADER.match(line):
            skipping, pending = True, []
            continue
        if skipping:
            if _TABLE_HEADER.match(line):
                skipping = False
                out.extend(pending)
            elif not line.strip() or line.lstrip().startswith("#"):
                pending.append(line)
                continue
            else:
                pending = []
                continue
        out.append(line)
    return "\n".join(out).rstrip() + "\n"


def _peer_block(p: dict) -> str:
    lines = ["", "[[peers]]", f"name = {_toml(p['name'])}"]
    lines.append(f"hosts = {_toml(p['hosts'])}  # Thunderbolt first, then the .local names")
    lines.append(f"user = {_toml(p['user'])}")
    if "path" in p:
        lines.append(f"path = {_toml(p['path'])}")
    return "\n".join(lines) + "\n"


def wanted_peers(data: dict, peers: list[PeerEntry]) -> list[dict]:
    """Peers by cluster.toml id; a peer already in the file keeps its own user and path."""
    old = {str(p.get("name")): p for p in data.get("peers", [])}
    names = {p.name for p in peers}
    for name, prev in old.items():
        if name in names:
            continue
        hosts = {str(h).lower() for h in prev.get("hosts", [])}
        for p in peers:
            if hosts & {h.lower() for h in p.hosts}:
                raise PeerRename(f"peer {name} would be renamed to {p.name}")
    out = []
    for p in peers:
        prev = old.get(p.name, {})
        entry = {"name": p.name, "hosts": list(p.hosts), "user": str(prev.get("user", p.user))}
        if "path" in prev:
            entry["path"] = str(prev["path"])
        out.append(entry)
    return out


def plan_config(text: str | None, peers: list[PeerEntry]) -> str:
    """The config text this node should have (before `docsync roots add-llm`)."""
    text = text or ""
    data = tomllib.loads(text) if text.strip() else {}
    roots = [str(r.get("name")) for r in data.get("roots", [])]
    out = text
    if "path" not in data and "documents" not in roots:
        out = f"path = {_toml(DOCUMENTS)}\n" + out
    want = wanted_peers(data, peers)
    have = [
        {
            k: (list(v) if k == "hosts" else v)
            for k, v in p.items()
            if k in ("name", "hosts", "user", "path")
        }
        for p in data.get("peers", [])
    ]
    if have != want:
        out = _strip_peers(out) + "".join(_peer_block(p) for p in want)
    if DESKTOP[0] not in roots:
        out = (
            out.rstrip()
            + "\n"
            + (
                f"\n[[roots]]  # added by `maccluster sync setup`\nname = {_toml(DESKTOP[0])}\n"
                f"path = {_toml(DESKTOP[1])}\n"
            )
        )
    check = tomllib.loads(out)
    names = [str(r.get("name")) for r in check.get("roots", [])]
    if len(names) != len(set(names)):
        raise ValueError(f"duplicate root names: {names}")
    return out


def missing_llm_roots(text: str) -> list[str]:
    data = tomllib.loads(text) if text.strip() else {}
    have = {str(r.get("name")) for r in data.get("roots", [])}
    return [n for n in LLM_ROOTS if n not in have]


def root_count(text: str) -> int:
    """Roots docsync sees: the top-level `path` (root "documents") plus every [[roots]]."""
    data = tomllib.loads(text)
    return int("path" in data) + len(data.get("roots", []))
