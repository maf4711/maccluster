"""Small macOS hooks: Notification Center, battery state. Never raise into the sync path."""

from __future__ import annotations

import platform
import subprocess


def is_macos() -> bool:
    return platform.system() == "Darwin"


def notify(title: str, text: str, subtitle: str = "") -> bool:
    """Notification Center via osascript. Returns False when unavailable; never raises."""
    if not is_macos():
        return False
    esc = lambda s: s.replace("\\", "\\\\").replace('"', '\\"')  # noqa: E731
    script = f'display notification "{esc(text[:230])}" with title "{esc(title)}"'
    if subtitle:
        script += f' subtitle "{esc(subtitle)}"'
    try:
        res = subprocess.run(["/usr/bin/osascript", "-e", script], capture_output=True, text=True, timeout=10)
        return res.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def parse_pmset_batt(text: str) -> bool | None:
    """True on battery, False on AC, None if unknown."""
    head = text.splitlines()[0] if text.strip() else ""
    if "Battery Power" in head:
        return True
    if "AC Power" in head:
        return False
    return None


def on_battery() -> bool:
    """True only when pmset positively reports battery power."""
    if not is_macos():
        return False
    try:
        out = subprocess.run(["/usr/bin/pmset", "-g", "batt"], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.TimeoutExpired):
        return False
    return parse_pmset_batt(out) is True
