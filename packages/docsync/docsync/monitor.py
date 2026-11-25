"""`docsync monitor`, the facts of this Mac: launchd agent, run/root locks, history.log, conflict copies,
deadline dumps, iCloud check state. Read-only: never syncs, never takes a lock, never writes state.

Also the origin line every `sync`/`plan` writes into its history.log block (who started the run):
    origin: <argv> | parent <ppid> <parent command, max 200 chars> | agent=<yes|heal|no>
yes = the launchd agent (--from-agent), heal = `docsync heal` (--from-heal), no = anything else."""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
import threading
import time
from pathlib import Path

from docsync import lock
from docsync.agent import LABEL

PARENT_MAX = 200
DEADLINE_WINDOW = 24 * 3600
STAMP = "%Y-%m-%d %H:%M:%S"
BLOCK_RE = re.compile(r"^=== (\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)(.*)$", re.M)
PEERS_RE = re.compile(r"^\s*peers\s+(\d+) ok, (\d+) failed(?: \([^)]*\))?(?:, (\d+) busy)?", re.M)
TOTAL_RE = re.compile(r"^══ TOTAL( \(DRY RUN\))?$", re.M)
FAILED_PEER_RE = re.compile(r"^── (\S+)(?: \[([^\]\n]+)\])?[^\n]*\n\s+FAILED after [^:\n]*: (.*)$", re.M)
FILES_RE = re.compile(r"^\s*files\s+([\d,]+) ", re.M)
CONFLICTS_RE = re.compile(r"^\s*deleted\s+[\d,]+\s+conflicts ([\d,]+)", re.M)
CONFLICT_BUDGET = 20.0  # seconds for the conflict scan; past it `conflicts` is null (the monitor stays bounded)
AGENT_MIN_VERSION = "1.3.2"  # the symlink-guard floor: an agent below it is "old"; bump deliberately
HELD_RE = re.compile(r"([\d,]+) held back by deletion guard")
AGENT_RE = re.compile(r" \| agent=(yes|heal|no)\s*$")
BY = {"yes": "agent", "heal": "heal", "no": "other"}
TOP_RE = re.compile(r"^\t([^\t=\n][^=\n]*?) = (.*)$", re.M)  # top-level `key = value` of launchctl print
ARGS_RE = re.compile(r"^\targuments = \{\n(.*?)^\t\}", re.M | re.S)


# ── origin line ─────────────────────────────────────────────────────────────

def _one_line(text: str) -> str:
    return " ".join(str(text).split("\n"))


def parent_command(ppid: int) -> str | None:
    """`ps -o command= -p <ppid>` (read-only); None when ps fails or the process is gone."""
    try:
        res = subprocess.run(["ps", "-o", "command=", "-p", str(ppid)], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    cmd = res.stdout.strip()
    return cmd if res.returncode == 0 and cmd else None


def origin_line(argv: list[str] | None = None, ppid: int | None = None, from_agent: bool = False,
                from_heal: bool = False) -> str:
    argv = sys.argv if argv is None else argv
    ppid = os.getppid() if ppid is None else ppid
    cmd = parent_command(ppid)
    parent = f"{ppid} {_one_line(cmd)[:PARENT_MAX]}" if cmd else "?"
    return f"origin: {_one_line(shlex.join(argv))} | parent {parent} | agent={'yes' if from_agent else 'heal' if from_heal else 'no'}"


# ── history.log ─────────────────────────────────────────────────────────────

def _num(m, group: int = 1) -> int:
    return int(m.group(group).replace(",", "")) if m and m.group(group) else 0


def _origin(body: str) -> dict | None:
    for ln in body.splitlines():
        if ln.startswith("origin: "):
            m = AGENT_RE.search(ln)
            return {"text": ln[len("origin: "):], "agent": None if m is None else m.group(1) == "yes",
                    "by": None if m is None else BY[m.group(1)]}
    return None


def _run(stamp: str, kind: str, block: str, now: float) -> dict:
    try:
        age = round(now - time.mktime(time.strptime(stamp, STAMP)), 1)
    except ValueError:
        age = None
    body = "\n".join(ln for ln in block.split("\n") if not ln.startswith("origin: "))  # free text: never parsed
    peers = PEERS_RE.search(body)
    failed = [{"peer": m.group(1), "root": m.group(2) or "", "reason": m.group(3).strip()}
              for m in FAILED_PEER_RE.finditer(body)]
    return {"time": stamp, "age_s": age, "kind": kind, "ok": _num(peers, 1), "failed": _num(peers, 2),
            "busy": _num(peers, 3), "files": _num(FILES_RE.search(body)), "conflicts": _num(CONFLICTS_RE.search(body)),
            "peer_lock_lost": body.count("peer lock lost"), "symlink_skipped": body.count("skipped (symlink"),
            "held_deletes": sum(int(n.replace(",", "")) for n in HELD_RE.findall(body)), "failed_peers": failed,
            "deadline_exceeded": kind == "deadline", "origin": _origin(block)}


def parse_history(text: str, now: float | None = None) -> dict:
    """{"last_run", "last_success", "last_synced", "last_agent_run"} of history.log, from the parsed
    TOTAL counts only. last_agent_run: the newest run whose origin says agent=yes (judges the agent's
    exit; None in logs from before origin lines).
    A run is a report block (DRY RUN and all-busy runs are left out) or a `deadline of N s exceeded`
    event. last_success: ok >= 1 and failed == 0 (busy allowed). last_synced: ok >= 1, whatever
    failed or was busy (this node synced with at least one peer; the stale rule uses it)."""
    now = time.time() if now is None else now
    heads = list(BLOCK_RE.finditer(text))
    found = {"last_run": None, "last_success": None, "last_synced": None, "last_agent_run": None}
    for i in range(len(heads) - 1, -1, -1):
        h = heads[i]
        body = text[h.end():heads[i + 1].start() if i + 1 < len(heads) else len(text)]
        total = TOTAL_RE.search(body)
        if total:
            if total.group(1):
                continue
            run = _run(h.group(1), "report", body, now)
            if run["ok"] == 0 and run["failed"] == 0 and run["busy"] > 0:
                continue
            if run["ok"] >= 1:
                found["last_synced"] = found["last_synced"] or run
                if run["failed"] == 0:
                    found["last_success"] = found["last_success"] or run
        elif re.search(r"deadline of \S+ s exceeded", h.group(2)):
            run = _run(h.group(1), "deadline", body, now)
        else:
            continue
        found["last_run"] = found["last_run"] or run
        if found["last_agent_run"] is None and (run["origin"] or {}).get("by") == "agent":
            found["last_agent_run"] = run
        if found["last_success"] is not None and found["last_agent_run"] is not None:
            break  # last_synced and last_run are at least as new
    return found


# ── launchd agent ───────────────────────────────────────────────────────────

def launchctl_print() -> tuple[int, str]:
    """(rc, output) of `launchctl print gui/<uid>/<LABEL>`. Test hook DOCSYNC_TEST_LAUNCHCTL: a file
    with that output (missing = not loaded)."""
    hook = os.environ.get("DOCSYNC_TEST_LAUNCHCTL")
    if hook:
        try:
            return 0, Path(hook).read_text()
        except OSError:
            return 113, ""
    try:
        res = subprocess.run(["launchctl", "print", f"gui/{os.getuid()}/{LABEL}"], capture_output=True, text=True,
                             timeout=10)
    except (OSError, subprocess.SubprocessError):
        return 1, ""
    return res.returncode, res.stdout


def agent_version(python: str, cache: dict | None = None) -> str | None:
    """`<python> -m docsync --version` -> "1.3.3"; None when it does not answer within 10 s.
    Run from / without PYTHONPATH, so it finds the docsync that interpreter imports under launchd."""
    if cache is not None and python in cache:
        return cache[python]
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    try:
        res = subprocess.run([python, "-m", "docsync", "--version"], capture_output=True, text=True, timeout=10,
                             cwd="/", env=env)
        m = re.search(r"^docsync (\S+)", res.stdout, re.M) if res.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        m = None
    found = m.group(1) if m else None
    if cache is not None:
        cache[python] = found
    return found


def foreign_reason(prog: list[str], cache: dict | None = None) -> tuple[str | None, str | None]:
    """(reason, agent's docsync version). OWN (reason None) when prog is `<python> -m docsync`, that python
    exists and its docsync is at least AGENT_MIN_VERSION; else "shim" (any other form, e.g. docsync-launchd.py),
    "missing interpreter" or "old <v>" ("old ?" when it does not answer)."""
    from docsync.probe import version
    if len(prog) != 3 or prog[1:] != ["-m", "docsync"]:
        return "shim", None
    if not os.access(prog[0], os.X_OK):
        return "missing interpreter", None
    v = agent_version(prog[0], cache)
    if v is None or version(v) < version(AGENT_MIN_VERSION):
        return f"old {v or '?'}", v
    return None, v


def parse_launchctl(rc: int, text: str, cache: dict | None = None) -> dict:
    """Agent facts from `launchctl print`; program_ok / foreign_reason from foreign_reason();
    loaded_age_s from the mtime of the plist it names (install-agent writes it, then loads it)."""
    agent = {"loaded": rc == 0, "state": None, "last_exit": None, "program": None, "program_ok": None,
             "interval_s": None, "max_runtime": None, "version": None, "foreign_reason": None, "loaded_age_s": None}
    if rc != 0:
        return agent
    top = dict(TOP_RE.findall(text))
    m = ARGS_RE.search(text)
    args = [a.strip() for a in m.group(1).splitlines() if a.strip()] if m else []
    if not args and top.get("program"):
        args = [top["program"]]
    prog = args[:args.index("sync")] if "sync" in args else args
    code = re.match(r"-?\d+", top.get("last exit code", ""))
    interval = re.match(r"(\d+)", top.get("run interval", ""))
    rt = args.index("--max-runtime") if "--max-runtime" in args else None
    reason, ver = foreign_reason(prog, cache)
    agent.update(state=top.get("state"), last_exit=int(code.group()) if code else None, program=prog,
                 program_ok=reason is None, foreign_reason=reason, version=ver,
                 interval_s=int(interval.group(1)) if interval else None,
                 max_runtime=_float(args[rt + 1]) if rt is not None and rt + 1 < len(args) else None)
    try:
        agent["loaded_age_s"] = round(time.time() - os.stat(top["path"]).st_mtime, 1)
    except (KeyError, OSError):
        pass
    return agent


def _float(text: str):
    try:
        v = float(text)
    except ValueError:
        return None
    return int(v) if v.is_integer() else v


# ── locks, conflicts, dumps, iCloud ─────────────────────────────────────────

def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _holder(path: Path, here: str) -> dict | None:
    h = lock.read_holder(path)
    if h is None:
        return None
    remote = bool(h.get("host")) and h["host"] != here  # pid of another Mac's run: not checkable here
    return {"pid": h["pid"], "started": h["started"], "command": h["command"], "host": h.get("host"),
            "alive": None if remote else _alive(h["pid"]), "lock": path.name}


def lock_holders(state_dir: Path) -> dict:
    """Records of run.lock and the root locks (read only, never flocked); alive = pid answers kill -0."""
    here = lock.host_name()
    roots = [_holder(p, here) for p in sorted((Path(state_dir) / "locks").glob("root-*.lock"))]
    return {"run_lock": _holder(Path(state_dir) / "run.lock", here), "root_locks": [h for h in roots if h]}


def conflict_copies(cfg) -> dict[str, list[str]]:
    """root name -> `*.conflict-*` paths, scanned like sync scans the root (includes honoured)."""
    from docsync.manifest import scan
    out = {}
    for r in cfg.effective_roots():
        c = cfg.for_root(r)
        files = scan(c.local_root, [".docsync*"], None, c.includes, None, c.case_insensitive)
        out[r.name] = sorted(p for p in files if ".conflict-" in p)
    return out


_SCAN: dict = {"thread": None, "box": None, "last": None}  # the one conflict scan of this process
_SCAN_LOCK = threading.Lock()


def bounded_conflicts(cfg, budget: float | None = None) -> dict[str, int] | None:
    """{root: count} of conflict_copies(), waiting at most `budget` seconds. The scan runs in a daemon
    thread; one that is late keeps running, and no second scan starts while it does (--interval):
    a late answer is the last finished scan's result, or None."""
    with _SCAN_LOCK:
        t = _SCAN["thread"]
        if t is None or not t.is_alive():
            if _SCAN["box"]:
                _SCAN["last"] = _SCAN["box"][0]
            box: list = []
            _SCAN["box"] = box

            def scan():
                try:
                    box.append({k: len(v) for k, v in conflict_copies(cfg).items()})
                except OSError:
                    pass  # unknown, like a scan that ran out of time
            t = _SCAN["thread"] = threading.Thread(target=scan, name="docsync-monitor-conflicts", daemon=True)
            t.start()
        box = _SCAN["box"]
    t.join(CONFLICT_BUDGET if budget is None else budget)
    with _SCAN_LOCK:
        if box:
            _SCAN["last"] = box[0]
            return box[0]
        return _SCAN["last"]


def deadline_dumps(log_dir: Path, now: float | None = None) -> list[str]:
    """Paths of the deadline-*.txt stack dumps in the log dir changed within the last 24 h, newest first."""
    now = time.time() if now is None else now
    found = []
    for p in Path(log_dir).glob("deadline-*.txt"):
        try:
            mt = p.stat().st_mtime
        except OSError:
            continue
        if now - mt <= DEADLINE_WINDOW:
            found.append((mt, str(p)))
    return [n for _, n in sorted(found, reverse=True)]


def icloud_state(state_dir: Path, node: str, now: float | None = None) -> dict | None:
    """The last icloud-check result for `node`: age and ok per root; None when absent or unreadable."""
    now = time.time() if now is None else now
    try:
        rec = json.loads((Path(state_dir) / f"icloud-{node}.json").read_text())
        checked = float(rec["checked_at"])
        roots = {str(k): bool(v.get("ok")) for k, v in rec["roots"].items()}
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None
    return {"checked_at": checked, "age_s": round(now - checked, 1), "roots": roots}


def local_node(cfg, state_dir: Path | None = None, log_dir: Path | None = None, now: float | None = None) -> dict:
    """Everything `monitor` shows for this Mac (`cfg` None: no config, conflicts unknown). Test hook
    DOCSYNC_TEST_VERSION: the version this node reports (a peer below MIN_PEER_VERSION in tests)."""
    from docsync import __version__, icloud, watchdog
    from docsync.config import STATE_DIR
    state_dir = Path(state_dir or STATE_DIR)
    now = time.time() if now is None else now
    try:
        history = (state_dir / "history.log").read_text(errors="replace")
    except OSError:
        history = ""
    node = icloud.node_name()
    return {"node": node, "state": "ok", "version": os.environ.get("DOCSYNC_TEST_VERSION") or __version__,
            "agent": parse_launchctl(*launchctl_print(), cache={}), "running": lock_holders(state_dir),
            **parse_history(history, now),
            "conflicts": None if cfg is None else bounded_conflicts(cfg),
            "deadline_dumps": deadline_dumps(log_dir or watchdog.log_dir(), now),
            "icloud": icloud_state(state_dir, node, now), "alerts": []}
