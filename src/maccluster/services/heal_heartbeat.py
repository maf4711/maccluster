"""Heal-loop heartbeat file for hang detection / watchdog kick."""

from __future__ import annotations

import json
import time
from pathlib import Path

from maccluster.config.paths import default_heal_heartbeat_path
from maccluster.constants import DEFAULT_HEAL_INTERVAL_S, HEAL_HEARTBEAT_STALE_FACTOR
from maccluster.domain.models import HealHeartbeat


def heartbeat_path() -> Path:
    return default_heal_heartbeat_path()


def write_heartbeat(
    *,
    ok: bool,
    exit_code: int,
    interval_seconds: float | None = None,
    path: Path | None = None,
) -> Path:
    p = path or heartbeat_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "ts": time.time(),
        "ok": bool(ok),
        "exit_code": int(exit_code),
        "interval_seconds": float(interval_seconds or DEFAULT_HEAL_INTERVAL_S),
    }
    p.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    return p


def read_heartbeat(
    *,
    path: Path | None = None,
    interval_seconds: float | None = None,
    now: float | None = None,
) -> HealHeartbeat:
    p = path or heartbeat_path()
    if not p.is_file():
        return HealHeartbeat(
            path=str(p),
            age_seconds=None,
            last_ok=None,
            last_exit_code=None,
            stale=True,
            detail="no heartbeat yet (heal --loop not running or never ticked)",
        )
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        ts = float(data.get("ts", 0))
        last_ok = bool(data.get("ok")) if "ok" in data else None
        last_exit = data.get("exit_code")
        last_exit_code = int(last_exit) if last_exit is not None else None
        interval = float(
            interval_seconds
            if interval_seconds is not None
            else data.get("interval_seconds") or DEFAULT_HEAL_INTERVAL_S
        )
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        return HealHeartbeat(
            path=str(p),
            age_seconds=None,
            last_ok=None,
            last_exit_code=None,
            stale=True,
            detail=f"heartbeat unreadable: {exc}",
        )

    t_now = time.time() if now is None else now
    age = max(0.0, t_now - ts) if ts > 0 else None
    limit = max(float(MIN_STALE_FLOOR_S), interval * HEAL_HEARTBEAT_STALE_FACTOR)
    stale = age is None or age > limit
    if age is None:
        detail = "heartbeat timestamp missing"
    elif stale:
        detail = (
            f"stale age={age:.0f}s (limit {limit:.0f}s = {HEAL_HEARTBEAT_STALE_FACTOR:g}×interval)"
        )
    else:
        detail = f"fresh age={age:.0f}s last_ok={last_ok}"
    return HealHeartbeat(
        path=str(p),
        age_seconds=age,
        last_ok=last_ok,
        last_exit_code=last_exit_code,
        stale=stale,
        detail=detail,
    )


MIN_STALE_FLOOR_S = 45.0
