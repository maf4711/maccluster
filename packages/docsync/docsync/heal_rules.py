"""The rule table of `docsync heal`: which actions are allowed for an alert on this node.

Coordinator mode is explicit (`docsync heal --coordinator`, only on the Mac that heals the others):
a peer set up by `maccluster remote-install` also has cluster.toml and maccluster, so their mere
presence must not make it act on other nodes."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

ADVISE_ONLY = {"deadline_dump": "summarize_dump", "conflicts": "advise_conflicts", "held_deletes": "advise_deletes"}
ICLOUD_SWITCH = "System Settings › Apple Account › iCloud › iCloud Drive › Desktop & Documents Folders"
UNREACHABLE_HINTS = ("answered a ping", "unreachable", "timed out", "no route to host", "host is down")


@dataclass
class Context:
    node: str
    coordinator: bool
    cluster_nodes: dict
    python: str
    state_dir: Path
    log_dir: Path
    maccluster: str | None
    maccluster_src: Path
    plist: Path
    uid: int
    wake_macs: dict = field(default_factory=dict)


def _all_peers_down(alert: dict) -> bool:
    """stale_success whose last run synced with no peer (all failed or busy): a local sync cannot help."""
    run = alert.get("last_run") or {}
    return bool(run) and not run.get("ok") and (run.get("failed") or 0) + (run.get("busy") or 0) > 0


def _peer_trouble(alert: dict, alerts) -> bool:
    """A sync cannot help this node now: the same pass has a peer_failed of this node or a
    peer_unreachable, or its last run synced with no peer."""
    node = alert.get("node")
    return _all_peers_down(alert) or any(o.get("code") == "peer_unreachable" or
                                         (o.get("code") == "peer_failed" and o.get("node") == node) for o in alerts)


def wake_targets(alert: dict, alerts=()) -> list[str]:
    """Peers to wake for stale_success / peer_failed: the unreachable ones of this alert, of this node's
    peer_failed and every peer_unreachable in the same pass."""
    out = list(unreachable_peers(alert))
    for o in alerts:
        if o.get("code") == "peer_failed" and o.get("node") == alert.get("node"):
            out += unreachable_peers(o)
        elif o.get("code") == "peer_unreachable" and alert.get("code") == "stale_success":
            out.append(str(o.get("node")))
    return list(dict.fromkeys(out))


def allowed_actions(alert: dict, ctx: Context, alerts=()) -> list[str]:
    """The actions heal may take for this alert on this node; [] = leave it to the user.
    `alerts`: every alert of this pass (stale_success looks at the peer alerts beside it)."""
    code, node = alert.get("code"), alert.get("node")
    local = node == ctx.node
    if not local and not ctx.coordinator:
        return []  # other nodes heal only themselves
    managed = ctx.coordinator and node in ctx.cluster_nodes
    if code == "agent_not_loaded":
        return ["reload_agent"] if local else ["setup_node"] if managed else []
    if code == "agent_exit":  # loaded but its last run failed: bootstrap would be a no-op, kickstart -k kills
        return ["notify"] if local else ["setup_node"] if managed else []
    if code in ("agent_foreign", "agent_no_max_runtime"):
        return ["setup_node"] if managed else []
    if code == "stale_success":
        if not local:
            return []
        if _peer_trouble(alert, alerts):
            return ["wake_peer", "notify"] if ctx.coordinator and wake_targets(alert, alerts) else ["notify"]
        return ["sync_now"]
    if code == "peer_unreachable":
        return [] if local else ["wake_peer"]
    if code == "peer_failed":
        return ["wake_peer", "notify"] if ctx.coordinator and unreachable_peers(alert) else ["notify"]
    if code == "peer_too_old":
        return ["update_peer"] if managed and not local else []
    if code in ADVISE_ONLY:
        return [ADVISE_ONLY[code]] if local else ["notify"]
    if code in ("non_agent_run", "icloud_incomplete"):
        return ["notify"]
    return []  # peer_lock_lost, never_synced (fresh install in its grace period) and unknown codes


def unreachable_peers(alert: dict) -> list[str]:
    """The peers whose failure says they did not answer: peer_failed {peers, reasons} (a reason is
    "<peer>[ [root]]: <text>"), or the last run's failed_peers [{peer, root, reason}] of the node."""
    data = alert.get("data") or {}
    pairs = []
    for r in data.get("reasons") or []:
        name, _, text = str(r).partition(": ")
        if name.split(" [")[0] in [str(p) for p in data.get("peers") or []]:
            pairs.append((name.split(" [")[0], text))
    pairs += [(str(f.get("peer")), str(f.get("reason", ""))) for f in (alert.get("last_run") or {}).get("failed_peers") or []
              if isinstance(f, dict)]
    out = []
    for name, text in pairs:
        if name not in out and any(h in text.lower() for h in UNREACHABLE_HINTS):
            out.append(name)
    return out


def rule_action(alert: dict, ctx: Context) -> str:
    acts = allowed_actions(alert, ctx)
    return acts[0] if acts else "none"


