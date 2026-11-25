"""Apple Foundation Model for `docsync heal` (Swift helper docsync-heal, compiled on first use like the
advisor). The model only CHOOSES among allowed actions (guided generation, validated again here) or
writes a short German summary. None whenever it cannot help: then the rule table decides.

Test hook: DOCSYNC_TEST_FM = path of a canned helper reply (JSON or text), or "unavailable"."""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

from docsync.advisor import helper_ready
from docsync.config import STATE_DIR

SWIFT_SRC = Path(__file__).resolve().parent / "resources" / "docsync-heal.swift"
HELPER = STATE_DIR / "bin" / "docsync-heal"
TIMEOUT = 20
MAX_SENTENCES = 3
COMPILE_MIN_S = 120  # swiftc only starts with at least this much of the caller's budget left


def needs_compile() -> bool:
    return not HELPER.exists() or HELPER.stat().st_mtime < SWIFT_SRC.stat().st_mtime


def _call(mode: str, prompt: str, budget: float | None = None) -> str | None:
    """The helper's stdout for `mode` (choose|summarize); None when unavailable, on timeout or error.
    budget: seconds the caller has left; a compile needs COMPILE_MIN_S of it and stops 60 s before."""
    hook = os.environ.get("DOCSYNC_TEST_FM")
    if hook:
        if hook == "unavailable":
            return None
        try:
            return Path(hook).read_text()
        except OSError:
            return None
    if budget is not None and needs_compile():
        if budget < COMPILE_MIN_S:
            return None
        ok, _why = helper_ready(SWIFT_SRC, HELPER, timeout=min(600, budget - 60))
    else:
        ok, _why = helper_ready(SWIFT_SRC, HELPER)
    if not ok:
        return None
    try:
        res = subprocess.run([str(HELPER), mode], input=prompt, text=True, capture_output=True, timeout=TIMEOUT)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return res.stdout if res.returncode == 0 else None


def availability() -> tuple[bool, str]:
    """(usable, reason) of the heal helper, for the smoke check."""
    ok, why = helper_ready(SWIFT_SRC, HELPER)
    if not ok:
        return False, why
    try:
        res = subprocess.run([str(HELPER), "--check"], text=True, capture_output=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, f"helper check failed: {e}"
    return res.returncode == 0, res.stdout.strip() or res.stderr.strip()


def choose_prompt(alerts: list[dict], context: dict, allowed: dict[str, list[str]]) -> str:
    rows = [{k: a.get(k) for k in ("node", "level", "code", "text")} for a in alerts if a.get("code") in allowed]
    choices = {code: [*acts, "none"] for code, acts in allowed.items()}
    return "\n".join(["## Kontext", json.dumps(context, ensure_ascii=False, sort_keys=True),
                      "## Alerts", json.dumps(rows, ensure_ascii=False),
                      "## Erlaubte Aktionen pro Code (genau eine wählen oder none)", json.dumps(choices, ensure_ascii=False)])


def choose(alerts: list[dict], context: dict, allowed: dict[str, list[str]], log=None,
           budget: float | None = None) -> list[dict] | None:
    """[{"code","action","reason"}] for codes in `allowed`; disallowed actions and unknown codes are
    dropped (and logged). None when the model is unavailable, timed out or answered garbage."""
    log = log or (lambda text: None)
    out = _call("choose", choose_prompt(alerts, context, allowed), budget)
    if out is None:
        return None
    try:
        data = json.loads(out)
    except ValueError:
        return None
    rows = data.get("decisions") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return None
    picked = []
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("code"), str) or not isinstance(row.get("action"), str):
            return None
        code, action = row["code"], row["action"]
        if code not in allowed:
            log(f"fm proposed unknown code {code}")
        elif action != "none" and action not in allowed[code]:
            log(f"fm proposed disallowed action {action}")
        else:
            picked.append({"code": code, "action": action, "reason": str(row.get("reason", ""))[:300]})
    return picked


def summarize(text: str, purpose: str, budget: float | None = None) -> str | None:
    """A German summary of `text` in at most 3 sentences; None when the model cannot give one."""
    out = _call("summarize", f"## Zweck\n{purpose}\n## Text\n{text[-6000:]}", budget)
    if out is None:
        return None
    try:
        data = json.loads(out)
        out = data["summary"] if isinstance(data, dict) and isinstance(data.get("summary"), str) else out
    except ValueError:
        pass
    sentences = re.split(r"(?<=[.!?])\s+", " ".join(out.split()))  # "session.py" is not a sentence end
    summary = " ".join(s for s in sentences[:MAX_SENTENCES] if s)
    return summary or None
