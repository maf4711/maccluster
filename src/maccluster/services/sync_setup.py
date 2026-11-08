"""`maccluster sync setup`: the same docsync set-up on every node of cluster.toml, from any one Mac.

Per node: docsync roots documents (the top-level `path`), desktop, llm-claude, llm-codex,
llm-gemini; peers = the other nodes by cluster.toml id (Thunderbolt address first, then the
`.local` names); one launchd agent `com.a321.docsync` (`docsync install-agent`, which runs every
15 min with `--max-runtime 1800`); `com.maccluster.sync-home` removed; `~/dotfiles` cloned or
fast-forwarded — a dirty or diverged clone is never touched. Every step is read first and
written only when it differs, so a second run on a set-up cluster writes nothing.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from maccluster.app_factory import AppContext
from maccluster.cluster_ssh import node_ssh_user
from maccluster.domain.models import Node
from maccluster.errors import CliError
from maccluster.services.config_service import load_and_bind_self
from maccluster.services.sync_docsync import _hosts
from maccluster.services.sync_setup_agent import (
    MIN_DOCSYNC,
    ensure_agent,
    parse_version,
    replace_foreign,
    retire_sync_home,
    stale_docsyncs,
)
from maccluster.services.sync_setup_config import (
    PeerEntry,
    PeerRename,
    missing_llm_roots,
    plan_config,
    root_count,
)
from maccluster.services.sync_setup_remote import NodeShell, connect, script

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
# Read-only: never take the flock (an agent run starting that moment would fail LOCK_NB and
# exit 3). run.lock keeps the last holder's record after release, so busy needs both: the pid
# on line 1 is alive AND that process started no later than the record's `started` (line 2,
# unix s; ±2 s). A process that started later reuses a recycled pid: the record is stale.
# Fail-safe: when the start times cannot be compared (no `started`, ps fails), it is busy.
LOCK_PROBE = """"$py" - <<'PY'
import os, subprocess, time
from pathlib import Path
p = Path(os.environ.get("DOCSYNC_STATE_DIR", "~/.local/state/docsync")).expanduser() / "run.lock"
try:
    lines = p.read_text(errors="replace").splitlines()
    pid = int(lines[0])
except (OSError, ValueError, IndexError):
    print("free"); raise SystemExit
try:
    os.kill(pid, 0)
except (ProcessLookupError, OverflowError):  # no such process
    print("free"); raise SystemExit
except PermissionError:  # alive, owned by another user
    pass
def proc_start(pid):
    out = subprocess.run(["/bin/ps", "-o", "lstart=", "-p", str(pid)], capture_output=True,
                         text=True, env={"LC_ALL": "C", "PATH": "/bin:/usr/bin"}).stdout.strip()
    return time.mktime(time.strptime(" ".join(out.split()), "%a %b %d %H:%M:%S %Y"))
try:
    started = float(lines[1])
    if proc_start(pid) > started + 2:
        print("free"); raise SystemExit  # a newer process got the recycled pid
except (IndexError, ValueError, OSError):
    pass  # cannot compare: fail-safe busy
try:
    from docsync import lock
    print("busy:", lock.busy_text(lock.read_holder(p)))
except Exception:
    print(f"busy: another run is in progress (pid {pid})")
PY
"""
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


def _check_docsync(sh: NodeShell) -> None:
    out = sh.run(script("docsync", DOCSYNC_PROBE, python=True), timeout=60).stdout or ""
    shown, ver = parse_version(out)
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
    except PeerRename as exc:
        raise _Skip(f"skipped: {sh.node.id} {exc}") from exc
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
        try:
            foreign_msg = replace_foreign(sh, dry_run=dry_run, stamp=stamp)
        except RuntimeError as exc:
            raise _Skip(str(exc)) from exc
        changed, cfg_msg = _config(sh, _peers_of(node, nodes), dry_run=dry_run, stamp=stamp)
    except _Skip as skip:
        return NodeSetup(node.id, True, False, "-", "-", str(skip)), False
    agent_state, agent_msg, ok = ensure_agent(sh, dry_run=dry_run)
    home_msg = retire_sync_home(sh, dry_run=dry_run, agent_ok=ok)
    dot_state, dot_msg = _dotfiles(sh, dry_run=dry_run)
    stale = stale_docsyncs(sh)
    parts = [p for p in (foreign_msg, cfg_msg, agent_msg, home_msg, dot_msg, *stale) if p]
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
