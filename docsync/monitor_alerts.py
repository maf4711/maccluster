"""The alerts of one node's monitor facts: a pure function, shared by `docsync monitor` and `heal`.

Every alert carries a stable machine `code` (never renamed; heal dispatches on it), a level, a
human text and code-specific `data`:
FAIL  agent_not_loaded, agent_exit {exit}, agent_foreign {program, reason}, stale_success {age_min}
WARN  agent_no_max_runtime, conflicts {count, roots}, deadline_dump {paths}, peer_lock_lost {count},
      non_agent_run {origin}, held_deletes {count}, icloud_incomplete {roots}, peer_failed {peers, reasons},
      never_synced {loaded_age_min, grace_min}, peer_unreachable {state}, peer_too_old {version}

A failed or unreachable peer is that peer's problem: exit 2 with failed peers in the agent's last
run (last_agent_run, else last_run) is not agent_exit; failed peers in the last run are peer_failed (WARN), and staleness counts any run where this node synced with at least one peer."""

from __future__ import annotations

from dataclasses import dataclass, field

SUCCESS_MAX_AGE = 2 * 3600
NEVER_SYNCED_GRACE = 30 * 60  # + 2 × the agent's interval after it was loaded
DEFAULT_INTERVAL = 900
TOO_OLD = "docsync too old on "


@dataclass(frozen=True)
class Alert:
    level: str  # "FAIL" | "WARN"
    code: str
    text: str
    data: dict = field(default_factory=dict)

    def to_dict(self, node: str | None = None) -> dict:
        d = {"level": self.level, "code": self.code, "text": self.text, "data": dict(self.data)}
        return {"node": node, **d} if node is not None else d


def fmt_age(secs) -> str:
    if secs is None:
        return "?"
    s = max(0, int(secs))
    if s < 3600:
        return f"{s // 60}m"
    if s < 48 * 3600:
        return f"{s // 3600}h {s % 3600 // 60:02d}m"
    return f"{s // 86400}d"


def _state_alert(node: dict) -> Alert:
    """A node that gave no facts: unreachable, too old, local directory or error."""
    state = str(node.get("state"))
    if state.startswith(TOO_OLD):
        return Alert("WARN", "peer_too_old", state, {"version": node.get("version")})
    return Alert("WARN", "peer_unreachable", state, {"state": state})


def _staleness(agent: dict, synced: dict | None) -> list[Alert]:
    """FAIL stale_success when this node last synced with a peer more than 2 h ago, or never and
    the agent has been loaded longer than the grace (2 × interval + 30 min); before that never_synced."""
    if synced is None:
        grace = 2 * (agent.get("interval_s") or DEFAULT_INTERVAL) + NEVER_SYNCED_GRACE
        loaded = agent.get("loaded_age_s")
        if agent.get("loaded") and (loaded is None or loaded < grace):
            return [Alert("WARN", "never_synced", "no sync yet since the agent was loaded",
                          {"loaded_age_min": None if loaded is None else int(loaded // 60), "grace_min": grace // 60})]
        return [Alert("FAIL", "stale_success", "no successful sync in history.log", {"age_min": None})]
    age = synced.get("age_s")
    if age is None or age > SUCCESS_MAX_AGE:
        return [Alert("FAIL", "stale_success", f"last success {fmt_age(age)} ago",
                      {"age_min": None if age is None else int(age // 60)})]
    return []


def alerts(node: dict) -> list[Alert]:
    """Alerts of one node's facts (a `nodes[]` entry of `monitor --json`). Pure: reads only `node`."""
    if node.get("state") != "ok":
        return [_state_alert(node)]
    out: list[Alert] = []
    agent, run = node.get("agent") or {}, node.get("last_run")
    if not agent.get("loaded"):
        out.append(Alert("FAIL", "agent_not_loaded", "agent not loaded"))
    else:
        judged = node.get("last_agent_run") or run  # the agent's own exit: its own last run
        failed = (judged or {}).get("failed") or 0
        if agent.get("last_exit") not in (None, 0, 2, 3) or (agent.get("last_exit") == 2 and not failed):
            out.append(Alert("FAIL", "agent_exit", f"agent last exit {agent['last_exit']}", {"exit": agent["last_exit"]}))
        if agent.get("program_ok") is False:
            prog, why = list(agent.get("program") or []), agent.get("foreign_reason")
            out.append(Alert("FAIL", "agent_foreign", f"foreign program {' '.join(prog) or '?'}" + (f" ({why})" if why else ""),
                             {"program": prog, "reason": why}))
        if not agent.get("max_runtime"):  # not passed, or 0 = no limit
            out.append(Alert("WARN", "agent_no_max_runtime", "agent runs without --max-runtime"))
    out += _staleness(agent, node.get("last_synced"))
    if run and run.get("failed"):
        peers = list(dict.fromkeys(f["peer"] for f in run.get("failed_peers") or []))
        reasons = [f"{f['peer']}{' [' + f['root'] + ']' if f.get('root') else ''}: {f['reason']}"
                   for f in run.get("failed_peers") or []]
        out.append(Alert("WARN", "peer_failed", f"{run['failed']} peer run(s) failed in the last run: "
                         + (", ".join(peers) or "?"), {"peers": peers, "reasons": reasons}))
    roots = {r: n for r, n in (node.get("conflicts") or {}).items() if n}
    if roots:
        n = sum(roots.values())
        out.append(Alert("WARN", "conflicts", f"{n} open conflict cop{'y' if n == 1 else 'ies'}",
                         {"count": n, "roots": roots}))
    dumps = list(node.get("deadline_dumps") or [])
    if dumps:
        names = ", ".join(p.rsplit("/", 1)[-1] for p in dumps)
        out.append(Alert("WARN", "deadline_dump", f"deadline dump within 24 h: {names}", {"paths": dumps}))
    if run and run.get("peer_lock_lost"):
        n = run["peer_lock_lost"]
        out.append(Alert("WARN", "peer_lock_lost", f"peer lock lost in the last run ({n})", {"count": n}))
    if run and run.get("held_deletes"):
        n = run["held_deletes"]
        out.append(Alert("WARN", "held_deletes", f"{n} deletions held back in the last run", {"count": n}))
    origin = (run or {}).get("origin")
    if origin and origin.get("by") == "other":  # heal's own syncs (agent=heal) are expected
        out.append(Alert("WARN", "non_agent_run", f"last run did not come from the agent: {origin['text']}",
                         {"origin": origin["text"]}))
    ic = node.get("icloud")
    bad = sorted(r for r, good in ((ic or {}).get("roots") or {}).items() if not good)
    if bad:
        out.append(Alert("WARN", "icloud_incomplete", f"iCloud check not ok: {', '.join(bad)}", {"roots": bad}))
    return out
