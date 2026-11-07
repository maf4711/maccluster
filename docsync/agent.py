"""launchd agent: run `docsync sync --all` every N minutes."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

LABEL = "com.a321.docsync"
PLIST = Path(f"~/Library/LaunchAgents/{LABEL}.plist").expanduser()
LOG_DIR = Path("~/Library/Logs/docsync").expanduser()


def plist_xml(interval_s: int, entry: str, watch_path: str | None = None) -> str:
    watch = (f"\n  <key>WatchPaths</key><array><string>{watch_path}</string></array>"
             f"\n  <key>ThrottleInterval</key><integer>60</integer>") if watch_path else ""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>{LABEL}</string>
  <key>ProgramArguments</key><array>
    <string>{sys.executable}</string><string>{entry}</string><string>sync</string><string>--all</string><string>--quiet</string><string>--from-agent</string>
  </array>
  <key>StartInterval</key><integer>{interval_s}</integer>{watch}
  <key>RunAtLoad</key><true/>
  <key>LowPriorityIO</key><true/>
  <key>ProcessType</key><string>Background</string>
  <key>StandardOutPath</key><string>{LOG_DIR}/agent.log</string>
  <key>StandardErrorPath</key><string>{LOG_DIR}/agent.err</string>
  <key>EnvironmentVariables</key><dict>
    <key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
    <key>HOME</key><string>{Path.home()}</string>
  </dict>
</dict></plist>
"""


def entry_point() -> str:
    """Path of the docsync package parent, run as `python3 -m docsync`? launchd needs a file → use bin shim."""
    return str(Path(__file__).resolve().parent.parent / "bin" / "docsync-launchd.py")


def install(interval_min: int, watch_path: str | None = None) -> str:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    PLIST.parent.mkdir(parents=True, exist_ok=True)
    PLIST.write_text(plist_xml(interval_min * 60, entry_point(), watch_path))
    lint = subprocess.run(["plutil", "-lint", str(PLIST)], text=True, capture_output=True)
    if lint.returncode != 0:
        raise RuntimeError(f"plist invalid: {lint.stdout.strip() or lint.stderr.strip()}")
    uid = os.getuid()
    subprocess.run(["launchctl", "bootout", f"gui/{uid}", str(PLIST)], capture_output=True)
    res = subprocess.run(["launchctl", "bootstrap", f"gui/{uid}", str(PLIST)], text=True, capture_output=True)
    if res.returncode != 0:
        raise RuntimeError(f"launchctl bootstrap failed: {res.stderr.strip()}")
    trigger = f" + on changes in {watch_path}" if watch_path else ""
    return f"installed {PLIST} — every {interval_min} min{trigger}, log {LOG_DIR}/agent.log"


def uninstall() -> str:
    uid = os.getuid()
    subprocess.run(["launchctl", "bootout", f"gui/{uid}", str(PLIST)], capture_output=True)
    if PLIST.exists():
        PLIST.unlink()
    return f"removed {PLIST}"


def status() -> str:
    if not PLIST.exists():
        return "agent: not installed"
    res = subprocess.run(["launchctl", "print", f"gui/{os.getuid()}/{LABEL}"], text=True, capture_output=True)
    state = "loaded" if res.returncode == 0 else "installed but not loaded"
    return f"agent: {state} ({PLIST})"
