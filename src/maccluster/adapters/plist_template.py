"""LaunchAgent plist template (no shell; absolute ProgramArguments)."""

from __future__ import annotations

import html


def render_heal_plist(
    *,
    label: str,
    program: str,
    config_path: str,
    throttle_interval: int = 30,
) -> str:
    """Render a User LaunchAgent plist for `maccluster heal --loop`."""
    label_e = html.escape(label)
    program_e = html.escape(program)
    config_e = html.escape(config_path)
    throttle = max(10, int(throttle_interval))
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>{label_e}</string>
  <key>ProgramArguments</key>
  <array>
    <string>{program_e}</string>
    <string>--config</string>
    <string>{config_e}</string>
    <string>heal</string>
    <string>--loop</string>
  </array>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>ThrottleInterval</key>
  <integer>{throttle}</integer>
  <key>StandardOutPath</key>
  <string>/tmp/maccluster-heal.log</string>
  <key>StandardErrorPath</key>
  <string>/tmp/maccluster-heal.err</string>
</dict>
</plist>
"""


def render_watchdog_plist(
    *,
    label: str,
    program: str,
    config_path: str,
    interval_seconds: int = 60,
) -> str:
    """User LaunchAgent: periodic ``maccluster heal --watchdog`` (hang detector)."""
    label_e = html.escape(label)
    program_e = html.escape(program)
    config_e = html.escape(config_path)
    interval = max(30, int(interval_seconds))
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>{label_e}</string>
  <key>ProgramArguments</key>
  <array>
    <string>{program_e}</string>
    <string>--config</string>
    <string>{config_e}</string>
    <string>heal</string>
    <string>--watchdog</string>
  </array>
  <key>RunAtLoad</key>
  <true/>
  <key>StartInterval</key>
  <integer>{interval}</integer>
  <key>StandardOutPath</key>
  <string>/tmp/maccluster-heal-watchdog.log</string>
  <key>StandardErrorPath</key>
  <string>/tmp/maccluster-heal-watchdog.err</string>
</dict>
</plist>
"""


def render_sync_plist(
    *,
    label: str,
    program: str,
    config_path: str,
    interval_seconds: int = 3600,
) -> str:
    """User LaunchAgent for periodic ``maccluster sync home`` (CCC schedule analogue)."""
    label_e = html.escape(label)
    program_e = html.escape(program)
    config_e = html.escape(config_path)
    interval = max(300, int(interval_seconds))
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>{label_e}</string>
  <key>ProgramArguments</key>
  <array>
    <string>{program_e}</string>
    <string>--config</string>
    <string>{config_e}</string>
    <string>sync</string>
    <string>home</string>
    <string>--no-progress</string>
    <string>--safetynet</string>
    <string>--verify</string>
    <string>--notify</string>
  </array>
  <key>RunAtLoad</key>
  <false/>
  <key>StartInterval</key>
  <integer>{interval}</integer>
  <key>StandardOutPath</key>
  <string>/tmp/maccluster-sync-home.log</string>
  <key>StandardErrorPath</key>
  <string>/tmp/maccluster-sync-home.err</string>
</dict>
</plist>
"""
