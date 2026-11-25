"""launchd agents: `docsync sync --all` every N minutes (each run capped by --max-runtime), and the
heal watcher `docsync heal --once` (com.a321.docsync-heal)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from docsync.watchdog import DEFAULT_MAX_RUNTIME

LABEL = "com.a321.docsync"
PLIST = Path(f"~/Library/LaunchAgents/{LABEL}.plist").expanduser()
LOG_DIR = Path("~/Library/Logs/docsync").expanduser()
HEAL_LABEL = "com.a321.docsync-heal"
HEAL_PLIST = Path(f"~/Library/LaunchAgents/{HEAL_LABEL}.plist").expanduser()
HEAL_MAX_RUNTIME = 600
# While this file exists no docsync code path loads an agent (install-agent, install-heal-agent, heal's
# reload_agent): an incident pause by `launchctl bootout` must not be undone. Its text is the reason.
HOLD_FILE = Path("~/.cache/docsync.hold")  # expanded on every check (HOME may change, e.g. in tests)
WATCH_THROTTLE_S = 900  # WatchPaths start a run at most every 15 min (= the StartInterval maccluster uses)


def _watchable(paths: list[str]) -> list[str]:
    """WatchPaths candidates minus folders managed by iCloud Desktop & Documents: iCloud touches them
    all the time, so each would start a full sync every throttle interval (StartInterval stays).
    Fail safe: when the detection fails for any path, nothing is watched."""
    from docsync import icloud
    try:
        return [p for p in paths if not icloud.is_icloud_backed(p)]
    except Exception:  # noqa: BLE001 — unknown means "maybe iCloud": interval only
        return []


def plist_xml(interval_s: int, entry: str | list[str], watch_path: str | list[str] | None = None,
              max_runtime: int = DEFAULT_MAX_RUNTIME, label: str = LABEL, command: list[str] | None = None,
              log: str = "agent") -> str:
    """`command` (default `sync --all --quiet --from-agent`) runs after the entry, then --max-runtime.
    A custom command also keeps its children alive after it exits (AbandonProcessGroup)."""
    argv = [sys.executable, entry] if isinstance(entry, str) else list(entry)
    argv += command or ["sync", "--all", "--quiet", "--from-agent"]
    argv += ["--max-runtime", str(max_runtime)]
    abandon = "\n  <key>AbandonProcessGroup</key><true/>" if command else ""
    paths = _watchable([watch_path] if isinstance(watch_path, str) else list(watch_path or []))
    watch = (f"\n  <key>WatchPaths</key><array>{''.join(f'<string>{p}</string>' for p in paths)}</array>"
             f"\n  <key>ThrottleInterval</key><integer>{WATCH_THROTTLE_S}</integer>") if paths else ""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>{label}</string>
  <key>ProgramArguments</key><array>
    {''.join(f"<string>{x}</string>" for x in argv)}
  </array>
  <key>StartInterval</key><integer>{interval_s}</integer>{watch}{abandon}
  <key>RunAtLoad</key><true/>
  <key>LowPriorityIO</key><true/>
  <key>ProcessType</key><string>Background</string>
  <key>Nice</key><integer>10</integer>
  <key>StandardOutPath</key><string>{LOG_DIR}/{log}.log</string>
  <key>StandardErrorPath</key><string>{LOG_DIR}/{log}.err</string>
  <key>EnvironmentVariables</key><dict>
    <key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
    <key>HOME</key><string>{Path.home()}</string>
  </dict>
</dict></plist>
"""


def entry_point() -> list[str]:
    """argv prefix launchd runs: the repo shim in a checkout, `python -m docsync` when installed."""
    shim = Path(__file__).resolve().parent.parent / "bin" / "docsync-launchd.py"
    return [sys.executable, str(shim)] if shim.exists() else [sys.executable, "-m", "docsync"]


def hold_reason() -> str | None:
    """"held: <reason>" while HOLD_FILE exists (reason = its first line), else None."""
    f = HOLD_FILE.expanduser()
    if not f.exists():
        return None
    try:
        reason = next((ln.strip() for ln in f.read_text(errors="replace").splitlines() if ln.strip()), "")
    except OSError:
        reason = ""
    return f"held: {reason or 'hold file present'} ({f} exists — remove it to allow agent loads)"


def _load(plist: Path, xml: str) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    plist.parent.mkdir(parents=True, exist_ok=True)
    plist.write_text(xml)
    lint = subprocess.run(["plutil", "-lint", str(plist)], text=True, capture_output=True)
    if lint.returncode != 0:
        raise RuntimeError(f"plist invalid: {lint.stdout.strip() or lint.stderr.strip()}")
    uid = os.getuid()
    subprocess.run(["launchctl", "bootout", f"gui/{uid}", str(plist)], capture_output=True)
    res = subprocess.run(["launchctl", "bootstrap", f"gui/{uid}", str(plist)], text=True, capture_output=True)
    if res.returncode != 0:
        raise RuntimeError(f"launchctl bootstrap failed: {res.stderr.strip()}")


def _unload(plist: Path) -> str:
    subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}", str(plist)], capture_output=True)
    if plist.exists():
        plist.unlink()
    return f"removed {plist}"


def install(interval_min: int, watch_path: str | list[str] | None = None) -> str:
    if held := hold_reason():
        return f"not installed {PLIST} — {held}"
    _load(PLIST, plist_xml(interval_min * 60, entry_point(), watch_path))
    shown = ", ".join([watch_path] if isinstance(watch_path, str) else (watch_path or []))
    trigger = f" + on changes in {shown}" if shown else ""
    return f"installed {PLIST} — every {interval_min} min{trigger}, log {LOG_DIR}/agent.log"


def uninstall() -> str:
    return _unload(PLIST)


def heal_plist_xml(interval_min: int, coordinator: bool = False) -> str:
    """`<python> -m docsync heal --once [--coordinator] --max-runtime 600` every `interval_min` minutes."""
    return plist_xml(interval_min * 60, [sys.executable, "-m", "docsync"], None, HEAL_MAX_RUNTIME, label=HEAL_LABEL,
                     command=["heal", "--once", *(["--coordinator"] if coordinator else [])], log="heal")


def install_heal(interval_min: int, coordinator: bool = False) -> str:
    if held := hold_reason():
        return f"not installed {HEAL_PLIST} — {held}"
    _load(HEAL_PLIST, heal_plist_xml(interval_min, coordinator))
    role = " as coordinator" if coordinator else ""
    return f"installed {HEAL_PLIST} — docsync heal{role} every {interval_min} min, log {LOG_DIR}/heal.log"


def uninstall_heal() -> str:
    return _unload(HEAL_PLIST)


def status() -> str:
    if not PLIST.exists():
        return "agent: not installed"
    res = subprocess.run(["launchctl", "print", f"gui/{os.getuid()}/{LABEL}"], text=True, capture_output=True)
    state = "loaded" if res.returncode == 0 else "installed but not loaded"
    return f"agent: {state} ({PLIST})"
