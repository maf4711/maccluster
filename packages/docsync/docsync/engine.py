"""3-way sync planner: local vs remote vs base (state after the last sync)."""

from __future__ import annotations

from dataclasses import dataclass, field

from docsync.manifest import Manifest


@dataclass
class Plan:
    push: list[str] = field(default_factory=list)       # local -> remote
    pull: list[str] = field(default_factory=list)       # remote -> local
    delete_local: list[str] = field(default_factory=list)
    delete_remote: list[str] = field(default_factory=list)
    conflicts: list[tuple[str, str]] = field(default_factory=list)  # (path, winner: local|remote)

    @property
    def empty(self) -> bool:
        return not (self.push or self.pull or self.delete_local or self.delete_remote or self.conflicts)

    @property
    def summary(self) -> str:
        return (f"push {len(self.push)}  pull {len(self.pull)}  "
                f"del-local {len(self.delete_local)}  del-remote {len(self.delete_remote)}  "
                f"conflicts {len(self.conflicts)}")


def _newer(a, b) -> bool:
    return a[0] > b[0]


def plan(local: Manifest, remote: Manifest, base: Manifest | None, policy: str = "newest") -> Plan:
    """Decide per path. base=None means first sync: union, newest wins, no deletes."""
    p = Plan()
    base = base or {}
    first = not base
    for path in sorted(set(local) | set(remote) | set(base)):
        L, R, B = local.get(path), remote.get(path), base.get(path)
        if L is not None and R is not None:
            if L == R:
                continue
            if first or B is None:  # both sides created the same path independently
                _conflict(p, path, L, R, policy)
            elif L == B:
                p.pull.append(path)
            elif R == B:
                p.push.append(path)
            else:
                _conflict(p, path, L, R, policy)
        elif L is not None:  # only local
            if B is None or L != B:
                p.push.append(path)  # new locally, or edited after remote deletion
            else:
                p.delete_local.append(path)  # unchanged here, deleted on the peer
        elif R is not None:  # only remote
            if B is None or R != B:
                p.pull.append(path)
            else:
                p.delete_remote.append(path)
        # else: in base only — both sides deleted; just drop from base
    return p


def _conflict(p: Plan, path: str, L, R, policy: str) -> None:
    if policy == "local":
        winner = "local"
    elif policy == "remote":
        winner = "remote"
    else:
        winner = "remote" if _newer(R, L) else "local"
    p.conflicts.append((path, winner))


def merged_base(local_after: Manifest) -> Manifest:
    """After a successful apply both sides match the local tree; store it as base."""
    return dict(local_after)


def conflict_name(path: str, stamp: str, host: str) -> str:
    """report.docx -> report.conflict-20261107T1750-hostname.docx"""
    head, _, tail = path.rpartition("/")
    stem, dot, ext = tail.rpartition(".")
    if not stem:  # no extension (or dotfile)
        stem, dot, ext = tail, "", ""
    new = f"{stem}.conflict-{stamp}-{host}{dot}{ext}"
    return f"{head}/{new}" if head else new
