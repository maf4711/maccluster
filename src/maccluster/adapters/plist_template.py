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
