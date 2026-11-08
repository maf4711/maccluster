"""`maccluster sync setup`: the same docsync set-up on every node of cluster.toml, from any one Mac.

Per node: docsync roots documents (the top-level `path`), desktop, llm-claude, llm-codex,
llm-gemini; peers = the other nodes by cluster.toml id (Thunderbolt address first, then the
`.local` names); one launchd agent `com.a321.docsync` (`docsync install-agent`, which runs every
15 min with `--max-runtime 1800`); `com.maccluster.sync-home` removed; `~/dotfiles` cloned or
fast-forwarded — a dirty or diverged clone is never touched. Every step is read first and
written only when it differs, so a second run on a set-up cluster writes nothing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from maccluster.app_factory import AppContext
from maccluster.cluster_ssh import node_ssh_user
from maccluster.constants import LAUNCH_AGENT_SYNC_LABEL
from maccluster.domain.models import Node
from maccluster.errors import CliError
from maccluster.services.config_service import load_and_bind_self
from maccluster.services.sync_docsync import _hosts
from maccluster.services.sync_setup_config import (
    PeerEntry,
    missing_llm_roots,
    plan_config,
    root_count,
)
from maccluster.services.sync_setup_remote import NodeShell, connect, script

MIN_DOCSYNC = (1, 3, 0)  # 1.2.x ignores root `includes` and would sync all of ~/.claude
AGENT_LABEL = "com.a321.docsync"
AGENT_INTERVAL_MIN = 15
AGENT_MAX_RUNTIME = 1800
LOCK_WAIT_S = 60
LOCK_POLL_S = 5
DOTFILES_REPO = "git@github.com:maf4711/dotfiles.git"
CONFIG = '"$HOME/.config/docsync/config.toml"'
_NO_GITHUB = ("permission denied", "could not read from remote", "host key verification")

DOCSYNC_PROBE = (
    'v=$("$py" -m docsync --version 2>&1) || { echo "docsync missing"; exit 0; }\n'
    'echo "$v"\n'
    '"$py" -m docsync roots add-llm --help >/dev/null 2>&1 && echo "add-llm ok"\n'
    '"$py" -m docsync icloud-check --help >/dev/null 2>&1 && echo "icloud-check ok"\n'
    "exit 0\n"
)
LOCK_PROBE = """"$py" - <<'PY'
import fcntl, os
from pathlib import Path
p = Path(os.environ.get("DOCSYNC_STATE_DIR", "~/.local/state/docsync")).expanduser() / "run.lock"
if not p.exists():
    print("free"); raise SystemExit
with open(p, "a+") as fh:
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        try:
            from docsync import lock
            print("busy:", lock.busy_text(lock.read_holder(p)))
        except Exception:
            print("busy: another run is in progress")
    else:
        print("free")
PY
"""
AGENT_STATUS = (
    f'[ -f "$HOME/Library/LaunchAgents/{AGENT_LABEL}.plist" ] && echo plist=1 || echo plist=0\n'
    f'launchctl print "gui/$(id -u)/{AGENT_LABEL}" 2>/dev/null || true\n'
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
GIT_ENV = (
    'export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH" GIT_TERMINAL_PROMPT=0 '
    "GIT_SSH_COMMAND='ssh -o BatchMode=yes -o ConnectTimeout=10'\n"
    'd="$HOME/dotfiles"\n'
)
DOTFILES_STATUS = GIT_ENV + (
    'if [ ! -e "$d" ]; then echo missing\n'
    'elif ! git -C "$d" rev-parse --git-dir >/dev/null 2>&1; then echo notgit\n'
    'elif [ -n "$(git -C "$d" status --porcelain 2>/dev/null | head -1)" ]; then echo dirty\n'
    "else echo clean; fi\n"
)


@dataclass(frozen=True)
class NodeSetup:
    name: str
    reachable: bool
    config_changed: bool
    agent_state: str
    dotfiles_state: str
    message: str


@dataclass(frozen=True)
class SetupResult:
    nodes: list[NodeSetup] = field(default_factory=list)
    exit_code: int = 0


class _Skip(Exception):
    """Ends one node's setup; the message says why."""


def _version(text: str) -> tuple[str, tuple[int, ...]]:
    m = re.search(r"docsync (\d+(?:\.\d+)*)", text)
    if not m:
        return "missing", ()
    return m.group(1), tuple(int(x) for x in m.group(1).split("."))


def _check_docsync(sh: NodeShell) -> None:
    out = sh.run(script("docsync", DOCSYNC_PROBE, python=True), timeout=60).stdout or ""
    shown, ver = _version(out)
    if ver < MIN_DOCSYNC or "add-llm ok" not in out or "icloud-check ok" not in out:
        raise _Skip(
            f"docsync {shown} on {sh.node.id}: run maccluster remote-install {sh.node.ip} first"
        )


def _wait_for_lock(ctx: AppContext, sh: NodeShell) -> None:
    waited = 0
    while True:
        out = (sh.run(script("lock", LOCK_PROBE, python=True), timeout=30).stdout or "").strip()
        if out.startswith("free"):
            return
        if waited >= LOCK_WAIT_S:
            holder = out.removeprefix("busy:").strip() or "docsync run lock is held"
            raise _Skip(f"skipped: {sh.node.id} {holder}")
        ctx.clock.sleep(LOCK_POLL_S)
        waited += LOCK_POLL_S


def _write_script(text: str, stamp: str) -> str:
    import base64

    b64 = base64.b64encode(text.encode()).decode()
    return (
        f'set -e; f={CONFIG}; mkdir -p "$(dirname "$f")"\n'
        f'if [ -f "$f" ]; then cp -p "$f" "$f.bak-{stamp}"; fi\n'
        f"printf %s '{b64}' | /usr/bin/base64 -D > \"$f.tmp\"\n"
        'mv "$f.tmp" "$f"\n'
    )


def _config(
    sh: NodeShell, peers: list[PeerEntry], *, dry_run: bool, stamp: str
) -> tuple[bool, str]:
    res = sh.run(script("read-config", f'f={CONFIG}; [ -f "$f" ] || exit 1; cat "$f"\n'))
    old = res.stdout if res.returncode == 0 else None
    try:
        new = plan_config(old, peers)
    except ValueError as exc:  # tomllib.TOMLDecodeError is a ValueError
        raise _Skip(f"docsync config on {sh.node.id} unreadable, left unchanged: {exc}") from exc
    llm = missing_llm_roots(new)
    names = " ".join(p.name for p in peers)
    detail = f"{root_count(new) + len(llm)} roots, peers {names}"
    if new == (old or "") and not llm:
        return False, "config unchanged"
    backup = f"backup config.toml.bak-{stamp}; " if old is not None else ""
    if dry_run:
        return True, f"would write config ({backup}{detail})"
    w = sh.run(script("write-config", _write_script(new, stamp)))
    if w.returncode != 0:
        raise _Skip(f"config not written on {sh.node.id}: {(w.stderr or w.stdout).strip()[:200]}")
    if llm:
        a = sh.run(script("add-llm", '"$py" -m docsync roots add-llm\n', python=True))
        if a.returncode != 0:
            raise _Skip(
                f"docsync roots add-llm failed on {sh.node.id}: {(a.stderr or a.stdout).strip()[:200]}"
            )
    return True, f"config written ({backup}{detail})"


def _sync_home(sh: NodeShell, *, dry_run: bool) -> str:
    out = sh.run(script("sync-home-status", SYNC_HOME_STATUS)).stdout or ""
    if "loaded=1" not in out and "plist=1" not in out:
        return ""
    if dry_run:
        return "would remove sync-home"
    sh.run(script("sync-home-remove", SYNC_HOME_REMOVE))
    return "sync-home removed"


def _agent_ok(out: str) -> tuple[bool, bool]:
    """(loaded, runs with --max-runtime 1800)."""
    loaded = "plist=1" in out and f"{AGENT_LABEL} = {{" in out
    return loaded, bool(re.search(rf"--max-runtime\s+{AGENT_MAX_RUNTIME}\b", out))


def _agent(sh: NodeShell, *, dry_run: bool) -> tuple[str, str, bool]:
    """(agent_state, message, ok)."""
    loaded, capped = _agent_ok(sh.run(script("agent-status", AGENT_STATUS)).stdout or "")
    if loaded and capped:
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
    loaded, capped = _agent_ok(sh.run(script("agent-status", AGENT_STATUS)).stdout or "")
    if loaded and capped:
        return "loaded", f"agent {AGENT_LABEL} loaded", True
    if loaded:
        state = f"loaded without --max-runtime {AGENT_MAX_RUNTIME}"
        return state, f"agent {state}", False
    err = (inst.stderr or inst.stdout or "").strip().splitlines()
    return "not loaded", f"agent not loaded: {err[-1][:160] if err else 'no output'}", False


def _git_failure(sh: NodeShell, text: str) -> tuple[str, str]:
    if any(w in text.lower() for w in _NO_GITHUB):
        return "no-github", f"dotfiles: no GitHub access on {sh.node.id}"
    lines = text.strip().splitlines()
    return "error", f"dotfiles: {lines[-1][:160] if lines else 'git failed'}"


def _dotfiles(sh: NodeShell, *, dry_run: bool) -> tuple[str, str]:
    state = (sh.run(script("dotfiles-status", DOTFILES_STATUS)).stdout or "").strip()
    if state == "missing":
        if dry_run:
            return "missing", "would clone dotfiles"
        r = sh.run(
            script("dotfiles-clone", GIT_ENV + f'git clone {DOTFILES_REPO} "$d"\n'), timeout=300
        )
        if r.returncode != 0:
            return _git_failure(sh, (r.stderr or "") + (r.stdout or ""))
        return "cloned", "dotfiles cloned"
    if state == "dirty":
        return "dirty", "dotfiles dirty — left unchanged"
    if state != "clean":
        return state or "unknown", "~/dotfiles is not a git clone — left unchanged"
    fetch = "" if dry_run else 'git -C "$d" fetch --quiet origin\n'
    body = GIT_ENV + fetch + 'git -C "$d" rev-list --left-right --count HEAD...@{u}\n'
    r = sh.run(script("dotfiles-fetch", body), timeout=120)
    if r.returncode != 0:
        return _git_failure(sh, (r.stderr or "") + (r.stdout or ""))
    try:
        ahead, behind = (int(x) for x in r.stdout.split()[:2])
    except ValueError:
        return "unknown", "dotfiles: cannot compare with origin"
    if ahead and behind:
        return "diverged", f"dotfiles diverged (ahead {ahead}, behind {behind}) — left unchanged"
    if ahead:
        return "ahead", f"dotfiles ahead {ahead} (not pushed) — left unchanged"
    if not behind:
        return "current", "dotfiles current"
    if dry_run:
        return "behind", f"would fast-forward dotfiles {behind} commits"
    ff = sh.run(script("dotfiles-ff", GIT_ENV + 'git -C "$d" merge --ff-only --quiet "@{u}"\n'))
    if ff.returncode != 0:
        return _git_failure(sh, (ff.stderr or "") + (ff.stdout or ""))
    return "fast-forwarded", f"dotfiles fast-forwarded {behind} commits"


def _peers_of(node: Node, nodes: tuple[Node, ...]) -> list[PeerEntry]:
    return [
        PeerEntry(n.id, tuple(_hosts(n, "tb")), node_ssh_user(n)) for n in nodes if n.id != node.id
    ]


def _setup_node(
    ctx, node: Node, self_node: Node, nodes, *, dry_run: bool, stamp: str
) -> tuple[NodeSetup, bool]:
    sh = connect(ctx, node, self_node=self_node)
    if sh is None:
        return NodeSetup(node.id, False, False, "-", "-", f"skipped: {node.id} unreachable"), False
    try:
        _check_docsync(sh)
        if not dry_run:
            _wait_for_lock(ctx, sh)
        changed, cfg_msg = _config(sh, _peers_of(node, nodes), dry_run=dry_run, stamp=stamp)
    except _Skip as skip:
        return NodeSetup(node.id, True, False, "-", "-", str(skip)), False
    home_msg = _sync_home(sh, dry_run=dry_run)
    agent_state, agent_msg, ok = _agent(sh, dry_run=dry_run)
    dot_state, dot_msg = _dotfiles(sh, dry_run=dry_run)
    parts = [p for p in (cfg_msg, agent_msg, home_msg, dot_msg) if p]
    return NodeSetup(node.id, True, changed, agent_state, dot_state, "  ".join(parts)), ok


def setup(ctx: AppContext, *, dry_run: bool = False, node: str | None = None) -> SetupResult:
    cfg, self_node = load_and_bind_self(ctx)
    ordered = [self_node, *[n for n in cfg.nodes if n.id != self_node.id]]
    if node is not None:
        ordered = [n for n in ordered if node in (n.id, str(n.ip))]
        if not ordered:
            known = ", ".join(n.id for n in cfg.nodes)
            raise CliError(f"unknown node {node!r} (cluster.toml: {known})", exit_code=2)
    stamp = ctx.clock.now().strftime("%Y%m%dT%H%M%SZ")
    results, all_ok = [], True
    for n in ordered:
        res, ok = _setup_node(ctx, n, self_node, cfg.nodes, dry_run=dry_run, stamp=stamp)
        results.append(res)
        all_ok = all_ok and ok
    return SetupResult(nodes=results, exit_code=0 if all_ok else 1)


def render_setup(result: SetupResult) -> str:
    return "\n".join(f"{n.name:<8} {n.message}" for n in result.nodes)
