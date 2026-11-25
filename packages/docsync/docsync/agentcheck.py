"""Is the launchd sync agent our own? Shared rule (same as docsync monitor's foreign_reason):
the agent is OK when its argv is `[<python>, "-m", "docsync", ...]`, that python exists, its
`-m docsync --version` is at least AGENT_MIN_VERSION, and --max-runtime is set.

`run(argv, timeout, env, cwd) -> (rc, output)` is injectable so callers (heal) route every
subprocess through their own runner."""

from __future__ import annotations

import os
import re
import subprocess

AGENT_MIN_VERSION = "1.3.2"  # the symlink-guard floor: an agent below it is "old"


def version(text) -> tuple[int, ...]:
    """'1.3.0' -> (1, 3, 0); missing or unreadable -> () (older than every release)."""
    nums = re.findall(r"\d+", text) if isinstance(text, str) else []
    return tuple(int(n) for n in nums[:3])


def _subprocess_run(argv, timeout=10, env=None, cwd=None) -> tuple[int, str]:
    try:
        res = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, env=env, cwd=cwd)
    except (OSError, subprocess.SubprocessError):
        return 1, ""
    return res.returncode, res.stdout


def agent_version(python: str, run=_subprocess_run) -> str | None:
    """`<python> -m docsync --version` -> "1.3.3"; None when it does not answer. Run from / without
    PYTHONPATH, so it finds the docsync that interpreter imports under launchd."""
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    rc, out = run([python, "-m", "docsync", "--version"], timeout=10, env=env, cwd="/")
    m = re.search(r"^docsync (\S+)", out or "", re.M) if rc == 0 else None
    return m.group(1) if m else None


def foreign_reason(prog: list[str], run=_subprocess_run) -> tuple[str | None, str | None]:
    """(reason, version). reason None = own agent; else "shim", "missing interpreter" or "old <v>"."""
    if len(prog) != 3 or prog[1:] != ["-m", "docsync"]:
        return "shim", None
    if not os.access(prog[0], os.X_OK):
        return "missing interpreter", None
    v = agent_version(prog[0], run)
    if v is None or version(v) < version(AGENT_MIN_VERSION):
        return f"old {v or '?'}", v
    return None, v


def launchctl_args(text: str) -> list[str]:
    """The `arguments = { … }` block of `launchctl print`."""
    args, inside = [], False
    for ln in text.splitlines():
        if ln.strip() == "arguments = {":
            inside = True
        elif inside and ln.strip() == "}":
            break
        elif inside and ln.strip():
            args.append(ln.strip())
    return args


def check(rc: int, text: str, run=_subprocess_run) -> tuple[bool, str]:
    """(ok, why) for `launchctl print gui/<uid>/com.a321.docsync` (rc, output)."""
    if rc != 0:
        return False, "agent not loaded"
    args = launchctl_args(text)
    prog = args[:args.index("sync")] if "sync" in args else args
    reason, ver = foreign_reason(prog, run)
    if reason:
        return False, f"agent is foreign ({reason}): {' '.join(prog) or '?'}"
    if "--max-runtime" not in args:
        return False, "agent has no --max-runtime"
    return True, f"agent loaded (docsync {ver})"
