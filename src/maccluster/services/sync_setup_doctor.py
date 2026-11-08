"""`doctor` finding `sync`: per node, does the `maccluster sync setup` set-up actually sync?

One finding per node: agent loaded and its last exit code (`launchctl print`), age of the last
successful docsync run (last report in history.log; warning above 2 h), the iCloud result docsync
stored in `icloud-<node>.json` (doctor never runs `icloud-check` itself) and the ~/dotfiles state.
A peer that does not answer is a warning, never a doctor failure.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from maccluster.cluster_ssh import node_ssh_user
from maccluster.domain.enums import CheckSeverity, ReachabilityState
from maccluster.domain.models import DoctorFinding, Node
from maccluster.services.sync_setup import AGENT_LABEL, DOTFILES_STATUS
from maccluster.services.sync_setup_remote import NodeShell, script

SUCCESS_WARN_S = 2 * 3600
_BLOCK = re.compile(r"^=== (\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)\s*$", re.M)
_SUCCESS = re.compile(r"peers\s+\d+ ok, 0 failed")


def _doctor_script(node_id: str) -> str:
    return (
        f'if out=$(launchctl print "gui/$(id -u)/{AGENT_LABEL}" 2>/dev/null); then\n'
        "  echo agent=loaded\n"
        "  echo \"$out\" | sed -n 's/^[[:space:]]*last exit code = \\(.*\\)$/exit=\\1/p' | head -1\n"
        "else echo agent=missing; fi\n"
        "dotstate() {\n" + DOTFILES_STATUS + "}\n"
        'st=$(dotstate); echo "dotfiles=$st"\n'
        'if [ "$st" = clean ]; then\n'
        '  echo "ahead_behind=$(git -C "$HOME/dotfiles" rev-list --left-right --count '
        'HEAD...@{u} 2>/dev/null)"\nfi\n'
        's="${DOCSYNC_STATE_DIR:-$HOME/.local/state/docsync}"\n'
        'if [ -f "$s/history.log" ]; then echo "== history"; tail -c 65536 "$s/history.log"; '
        'echo; echo "== end"; fi\n'
        f'if [ -f "$s/icloud-{node_id}.json" ]; then echo "== icloud"; cat "$s/icloud-{node_id}.json"; '
        'echo; echo "== end"; fi\n'
    )


def _section(out: str, name: str) -> str | None:
    m = re.search(rf"^== {name}\n(.*?)^== end$", out, re.S | re.M)
    return m.group(1) if m else None


def _kv(out: str, key: str) -> str:
    m = re.search(rf"^{key}=(.*)$", out, re.M)
    return m.group(1).strip() if m else ""


def _ago(seconds: float) -> str:
    if seconds < 3600:
        return f"{int(seconds // 60)} min ago"
    if seconds < 48 * 3600:
        return f"{int(seconds // 3600)} h ago"
    return f"{int(seconds // 86400)} d ago"


def last_success(history: str) -> datetime | None:
    """Start time of the newest history.log report whose peers all succeeded (this Mac's time)."""
    marks = list(_BLOCK.finditer(history))
    for i in range(len(marks) - 1, -1, -1):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(history)
        block = history[marks[i].end() : end]
        if "TOTAL" in block and _SUCCESS.search(block):
            return datetime.strptime(marks[i].group(1), "%Y-%m-%d %H:%M:%S").astimezone()
    return None


def _assess(out: str, now: datetime) -> tuple[list[str], bool]:
    """(summary parts, any warning)."""
    parts, warn = [], False
    if _kv(out, "agent") == "loaded":
        code = _kv(out, "exit") or "?"
        parts.append(f"agent loaded (last exit {code})")
        warn |= code not in ("0", "(never exited)")
    else:
        parts.append(f"agent {AGENT_LABEL} not loaded")
        warn = True
    when = last_success(_section(out, "history") or "")
    if when is None:
        parts.append("no successful docsync run in history.log")
        warn = True
    else:
        age = (now - when).total_seconds()
        parts.append(f"last docsync success {_ago(max(0.0, age))}")
        warn |= age > SUCCESS_WARN_S
    raw = _section(out, "icloud")
    if raw is None:
        parts.append("icloud not checked")
    else:
        try:
            roots = json.loads(raw).get("roots", {})
            bad = sorted(r for r, v in roots.items() if not v.get("ok"))
        except (ValueError, AttributeError):
            bad = ["unreadable"]
        parts.append(f"icloud not ok: {', '.join(bad)}" if bad else "icloud ok")
        warn |= bool(bad)
    dot = _kv(out, "dotfiles") or "unknown"
    if dot == "clean":
        try:
            ahead, behind = (int(x) for x in _kv(out, "ahead_behind").split()[:2])
        except ValueError:
            ahead = behind = 0
        if ahead and behind:
            dot = f"diverged (ahead {ahead}, behind {behind})"
        elif ahead:
            dot = f"ahead {ahead} (not pushed)"
        elif behind:
            dot = f"behind {behind}"
    parts.append(f"dotfiles {dot}")
    warn |= not (dot == "clean" or dot.startswith("behind"))
    return parts, warn


def sync_findings(
    ctx, cfg, self_node: Node, *, peer_states: dict[str, ReachabilityState]
) -> list[DoctorFinding]:
    local_cfg = Path.home() / ".config" / "docsync" / "config.toml"
    if not local_cfg.is_file():
        return [
            DoctorFinding(
                "sync",
                CheckSeverity.INFO,
                "docsync not set up on this Mac",
                "run: maccluster sync setup",
            )
        ]
    now = ctx.clock.now()
    findings = []
    for node in [self_node, *[n for n in cfg.nodes if n.id != self_node.id]]:
        if node.id != self_node.id and peer_states.get(node.id) != ReachabilityState.UP:
            findings.append(
                DoctorFinding(
                    "sync", CheckSeverity.WARN, f"{node.id} unreachable — sync state unknown"
                )
            )
            continue
        host = None if node.id == self_node.id else str(node.ip)
        sh = NodeShell(ctx, node, str(self_node.ip), host=host, user=node_ssh_user(node))
        err = ""
        try:
            res = sh.run(script("doctor", _doctor_script(node.id)), timeout=20)
        except Exception as exc:  # a runner error is a finding, not a doctor crash
            res, err = None, str(exc)
        if res is None or res.returncode != 0:
            detail = err if res is None else (res.stderr or res.stdout or "").strip()[:200]
            findings.append(
                DoctorFinding(
                    "sync", CheckSeverity.WARN, f"{node.id} sync state not readable", detail
                )
            )
            continue
        parts, warn = _assess(res.stdout or "", now)
        sev = CheckSeverity.WARN if warn else CheckSeverity.OK
        findings.append(DoctorFinding("sync", sev, f"{node.id} " + ", ".join(parts)))
    return findings
