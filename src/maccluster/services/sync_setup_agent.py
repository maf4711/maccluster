"""The one docsync launchd agent per node, and the other docsync installs that could undo it.

The agent is ours only when launchd runs the interpreter setup probed (`$py`, docsync >= 1.3)
with `-m docsync` and `--max-runtime 1800`. Any other program under the label (a repo shim, a
separate docsync-lan pipx venv on 1.2.x) is booted out before llm-* roots are written: docsync
1.2.x ignores root `includes` and would sync all of ~/.claude, including .credentials.json.
"""

from __future__ import annotations

import json
import re
import shlex
from dataclasses import dataclass

from maccluster.constants import LAUNCH_AGENT_SYNC_LABEL
from maccluster.services.sync_setup_remote import NodeShell, script

AGENT_LABEL = "com.a321.docsync"
AGENT_INTERVAL_MIN = 15
AGENT_MAX_RUNTIME = 1800
MIN_DOCSYNC = (1, 3, 0)  # 1.2.x ignores root `includes` and would sync all of ~/.claude

AGENT_PLIST = f'"$HOME/Library/LaunchAgents/{AGENT_LABEL}.plist"'
AGENT_STATUS = (
    f"p={AGENT_PLIST}\n"
    'if [ -f "$p" ]; then echo plist=1\n'
    '  echo "plistargs=$(plutil -extract ProgramArguments json -o - "$p" 2>/dev/null | tr -d \'\\n\')"\n'
    "else echo plist=0; fi\n"
    'echo "py=$py"\n'
    f'launchctl print "gui/$(id -u)/{AGENT_LABEL}" 2>/dev/null || true\n'
)
AGENT_BOOTOUT = f'launchctl bootout "gui/$(id -u)/{AGENT_LABEL}" 2>/dev/null || true\n'
OTHER_DOCSYNCS = (
    "for d in $(which -a docsync 2>/dev/null); do\n"
    '  echo "other=$d|$("$d" --version 2>/dev/null | head -1)"\n'
    "done\n"
    'c="$HOME/.local/pipx/venvs/docsync-lan/bin/python"\n'
    'if [ -x "$c" ] && [ "$c" != "$py" ]; then\n'
    '  echo "other=$c -m docsync|$("$c" -m docsync --version 2>/dev/null | head -1)"\n'
    "fi\n"
)
SYNC_HOME_PLIST = f'"$HOME/Library/LaunchAgents/{LAUNCH_AGENT_SYNC_LABEL}.plist"'
SYNC_HOME_STATUS = (
    f'l=0; launchctl print "gui/$(id -u)/{LAUNCH_AGENT_SYNC_LABEL}" >/dev/null 2>&1 && l=1\n'
    f'f=0; [ -e {SYNC_HOME_PLIST} ] && f=1\necho "loaded=$l plist=$f"\n'
)
SYNC_HOME_REMOVE = (
    f'launchctl bootout "gui/$(id -u)/{LAUNCH_AGENT_SYNC_LABEL}" 2>/dev/null || true\n'
    f"rm -f {SYNC_HOME_PLIST}\n"
)


def parse_version(text: str) -> tuple[str, tuple[int, ...]]:
    m = re.search(r"docsync (\d+(?:\.\d+)*)", text)
    if not m:
        return "?", ()
    return m.group(1), tuple(int(x) for x in m.group(1).split("."))


def _pair(args: tuple[str, ...], first: str, second: str) -> bool:
    return any(a == first and b == second for a, b in zip(args, args[1:], strict=False))


@dataclass(frozen=True)
class AgentState:
    plist: bool
    loaded: bool
    program: str
    arguments: tuple[str, ...]
    py: str
    plist_args: tuple[str, ...] = ()  # ProgramArguments in the plist on disk

    @property
    def plist_foreign(self) -> bool:
        """The plist on disk would start another program than `$py -m docsync` at next login."""
        args = self.plist_args
        if not self.plist:
            return False
        return not (args and self.py and args[0] == self.py and _pair(args, "-m", "docsync"))

    @property
    def own(self) -> bool:
        """launchd runs the probed interpreter with `-m docsync` (exact path: two venvs share
        one base interpreter, so a resolved path would not tell them apart)."""
        return (
            self.loaded
            and bool(self.py)
            and self.program == self.py
            and _pair(self.arguments, "-m", "docsync")
        )

    @property
    def capped(self) -> bool:
        return _pair(self.arguments, "--max-runtime", str(AGENT_MAX_RUNTIME))

    @property
    def ok(self) -> bool:
        return self.plist and self.own and self.capped


def parse_agent(out: str) -> AgentState:
    m = re.search(r"^\s*arguments = \{\n(.*?)^\s*\}", out, re.M | re.S)
    args = tuple(x.strip() for x in m.group(1).splitlines() if x.strip()) if m else ()
    prog = re.search(r"^\s*program = (.+)$", out, re.M)
    py = re.search(r"^py=(.*)$", out, re.M)
    raw = re.search(r"^plistargs=(.*)$", out, re.M)
    try:
        plist_args = tuple(str(a) for a in json.loads(raw.group(1))) if raw else ()
    except (ValueError, TypeError):
        plist_args = ()
    return AgentState(
        plist="plist=1" in out,
        loaded=f"{AGENT_LABEL} = {{" in out,
        program=prog.group(1).strip() if prog else (args[0] if args else ""),
        arguments=args,
        py=py.group(1).strip() if py else "",
        plist_args=plist_args,
    )


def agent_status(sh: NodeShell) -> AgentState:
    return parse_agent(sh.run(script("agent-status", AGENT_STATUS, python=True)).stdout or "")


def foreign_version(sh: NodeShell, st: AgentState, *, timeout: float) -> str:
    """docsync version of a foreign agent: its argv up to `sync`, plus --version."""
    argv = list(st.arguments or st.plist_args) or [st.program]
    prefix = argv[: argv.index("sync")] if "sync" in argv else argv[:1]
    cmd = shlex.join([*prefix, "--version"]) + " 2>&1\n"
    out = sh.run(script("agent-version", cmd), timeout=timeout).stdout
    return parse_version(out or "")[0]


def stale_docsyncs(sh: NodeShell) -> list[str]:
    """Warnings for other docsync installs older than 1.3.0 on the node."""
    out = sh.run(script("docsync-others", OTHER_DOCSYNCS, python=True)).stdout or ""
    warnings = []
    for line in re.findall(r"^other=(.*)$", out, re.M):
        path, _, ver = line.partition("|")
        shown, parsed = parse_version(ver)
        if parsed and parsed < MIN_DOCSYNC:
            warnings.append(f"stale docsync {shown} at {path} on {sh.node.id} — upgrade or remove")
    return warnings


def replace_foreign(sh: NodeShell, *, dry_run: bool, stamp: str) -> str:
    """Before llm-* roots exist: boot out a loaded com.a321.docsync that is not ours, and move a
    foreign plist on disk to `<plist>.foreign-<stamp>` (a paused foreign agent would come back
    at the next login, e.g. when install-agent fails). Returns a message ('' if nothing to do);
    raises RuntimeError when the foreign agent stays loaded or its plist cannot be moved."""
    st = agent_status(sh)
    msgs = []
    if st.loaded and not st.own:
        what = f"foreign agent {st.program} (docsync {foreign_version(sh, st, timeout=30)})"
        if dry_run:
            msgs.append(f"would replace {what}")
        else:
            sh.run(script("agent-bootout", AGENT_BOOTOUT))
            st = agent_status(sh)
            if st.loaded:
                raise RuntimeError(
                    f"{what} on {sh.node.id} could not be booted out — llm-* roots not written"
                )
            msgs.append(f"{what} booted out")
    if st.plist_foreign:
        program = st.plist_args[0] if st.plist_args else "unreadable ProgramArguments"
        if dry_run:
            msgs.append(f"would move foreign agent plist aside: {program}")
        else:
            mv = sh.run(
                script(
                    "agent-plist-aside",
                    f'mv -f {AGENT_PLIST} {AGENT_PLIST[:-1]}.foreign-{stamp}"\n',
                )
            )
            if mv.returncode != 0 or agent_status(sh).plist:
                raise RuntimeError(
                    f"foreign agent plist on {sh.node.id} could not be moved aside — llm-* roots not written"
                )
            msgs.append(f"foreign agent plist moved aside: {program}")
    return "  ".join(msgs)


def ensure_agent(sh: NodeShell, *, dry_run: bool) -> tuple[str, str, bool]:
    """(agent_state, message, ok): ours, loaded, every 15 min with --max-runtime 1800."""
    if agent_status(sh).ok:
        return "loaded", "agent loaded", True
    if dry_run:
        return "would install", f"would install agent {AGENT_LABEL}", True
    inst = sh.run(
        script(
            "install-agent",
            f'"$py" -m docsync install-agent --interval {AGENT_INTERVAL_MIN}\n',
            python=True,
        )
    )
    st = agent_status(sh)
    if st.ok:
        return "loaded", f"agent {AGENT_LABEL} loaded", True
    if st.own:
        state = f"loaded without --max-runtime {AGENT_MAX_RUNTIME}"
        return state, f"agent {state}", False
    if st.loaded:
        return "foreign", f"agent runs {st.program}, not {st.py}", False
    err = (inst.stderr or inst.stdout or "").strip().splitlines()
    return "not loaded", f"agent not loaded: {err[-1][:160] if err else 'no output'}", False


def retire_sync_home(sh: NodeShell, *, dry_run: bool, agent_ok: bool) -> str:
    """Remove com.maccluster.sync-home, but only once docsync's agent runs."""
    out = sh.run(script("sync-home-status", SYNC_HOME_STATUS)).stdout or ""
    if "loaded=1" not in out and "plist=1" not in out:
        return ""
    if not agent_ok:
        return "sync-home kept (agent not loaded)"
    if dry_run:
        return "would remove sync-home"
    sh.run(script("sync-home-remove", SYNC_HOME_REMOVE))
    return "sync-home removed"
