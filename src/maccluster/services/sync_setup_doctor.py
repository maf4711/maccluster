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
from maccluster.services.sync_setup import DOTFILES_STATUS, self_first
from maccluster.services.sync_setup_agent import (
    AGENT_LABEL,
    MIN_DOCSYNC,
    foreign_version,
    parse_agent,
    parse_version,
)
from maccluster.services.sync_setup_remote import PY_FIND, NodeShell, script

SUCCESS_WARN_S = 2 * 3600
_BLOCK = re.compile(r"^=== (\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)\s*$", re.M)
# at least one peer synced, none failed, none skipped as busy (an all-busy run moved nothing)
_SUCCESS = re.compile(r"peers\s+[1-9]\d* ok, 0 failed(?!.*busy)")


def _doctor_script(node_id: str) -> str:
    return (
        PY_FIND + 'echo "py=$py"\n'
        '[ -n "$py" ] && echo "pyver=$("$py" -m docsync --version 2>/dev/null | head -1)"\n'
        f'if out=$(launchctl print "gui/$(id -u)/{AGENT_LABEL}" 2>/dev/null); then\n'
        "  echo agent=loaded\n"
        "  echo \"$out\" | sed -n 's/^[[:space:]]*last exit code = \\(.*\\)$/exit=\\1/p' | head -1\n"
        '  echo "== agent"; printf \'%s\\n\' "$out"; echo "== end"\n'
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
    """Start time of the newest real (not dry-run) history.log report in which at least one peer
    synced and none failed or was busy (this Mac's time)."""
    marks = list(_BLOCK.finditer(history))
    for i in range(len(marks) - 1, -1, -1):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(history)
        block = history[marks[i].end() : end]
        if "TOTAL" in block and "DRY RUN" not in block and _SUCCESS.search(block):
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


def _agent_program(sh: NodeShell, out: str) -> tuple[list[str], bool]:
    """Warnings when com.a321.docsync runs another program than maccluster's docsync, or a
    docsync older than 1.3.0 (which ignores root `includes`)."""
    section = _section(out, "agent")
    if section is None:
        return [], False
    st = parse_agent(f"plist=1\npy={_kv(out, 'py')}\n{section}")
    min_shown = ".".join(str(x) for x in MIN_DOCSYNC)
    if st.own:
        shown, ver = parse_version(_kv(out, "pyver"))
        if ver and ver < MIN_DOCSYNC:
            return [f"agent docsync {shown} < {min_shown}"], True
        return [], False
    parts = [f"agent runs {st.program}, not maccluster's docsync — run maccluster sync setup"]
    try:
        shown, ver = parse_version(f"docsync {foreign_version(sh, st, timeout=10)}")
    except Exception:  # an unreadable version is no reason to fail the finding
        shown, ver = "?", ()
    if ver and ver < MIN_DOCSYNC:
        parts.append(f"agent docsync {shown} < {min_shown}")
    return parts, True


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
    for node in self_first(cfg.nodes, self_node):
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
            res = sh.run(script("doctor", _doctor_script(node.id)), timeout=10)
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
        agent_parts, agent_warn = _agent_program(sh, res.stdout or "")
        parts[1:1] = agent_parts
        warn |= agent_warn
        sev = CheckSeverity.WARN if warn else CheckSeverity.OK
        findings.append(DoctorFinding("sync", sev, f"{node.id} " + ", ".join(parts)))
    return findings
