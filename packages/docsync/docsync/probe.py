"""Run this package's own command on a peer over ssh (icloud-check and monitor answer for themselves).

The peer runs PROBE_SH under /bin/sh with PATH extended: its `docsync`, else the Python next to
`maccluster` that can import docsync. Then `"$@" <args>` runs the subcommand with that docsync."""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess

# Runs on the peer under /bin/sh: its own docsync, else the interpreter maccluster runs on.
PROBE_SH = r'''
PATH=/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:$PATH; export PATH
if command -v docsync >/dev/null 2>&1; then set -- docsync
else
  set --
  m=$(command -v maccluster 2>/dev/null) && m=$(realpath "$m" 2>/dev/null || readlink -f "$m" 2>/dev/null || echo "$m")
  for py in "${m%/*}/python" "$HOME/.local/pipx/venvs/maccluster/bin/python" "$HOME/.local/share/maccluster/venv/bin/python"; do
    if [ -x "$py" ] && "$py" -c "import docsync" 2>/dev/null; then set -- "$py" -m docsync; break; fi
  done
  [ $# -gt 0 ] || { echo "docsync not found on this Mac" >&2; exit 127; }
fi
'''


def transport(p, path: str, ssh_cmd=None):
    """Transport to the api.PeerSpec `p` (resolved host) for `path`; `ssh_cmd(peer name)` overrides ssh (tests)."""
    from docsync.config import Peer
    from docsync.hosts import transport_for
    t = transport_for(Peer(p.name, [p.host], p.user), p.host, path, bind=p.bind_ip)
    if ssh_cmd is not None and hasattr(t, "ssh_cmd"):
        t.ssh_cmd = ssh_cmd(p.name)
    return t


def run(t, args: str, timeout: float, connect_timeout: int | None = None) -> subprocess.CompletedProcess:
    """`docsync <args>` on the peer behind transport `t` (args already shell-quoted). On timeout the
    whole ssh process group is killed (nothing it started keeps the pipes open), then TimeoutExpired."""
    cmd = list(t.ssh_cmd)
    if connect_timeout:  # ssh keeps the first value of an option: put it before the defaults
        opt = ["-o", f"ConnectTimeout={connect_timeout}"]
        cmd = cmd[:1] + opt + cmd[1:] if cmd[0] == "ssh" else cmd + opt
    p = subprocess.Popen([*cmd, t.userhost, "/bin/sh -s"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, text=True, start_new_session=True)
    try:
        out, err = p.communicate(PROBE_SH + f'"$@" {args}\n', timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except OSError:
            pass
        p.communicate()
        raise
    return subprocess.CompletedProcess(p.args, p.returncode, out, err)


def unknown_command(res: subprocess.CompletedProcess) -> bool:
    """argparse of a docsync that does not know the subcommand."""
    return res.returncode != 0 and "invalid choice" in res.stderr


def last_json(stdout: str):
    """The JSON object on the last line of stdout. Raises ValueError (also for empty output)."""
    lines = stdout.strip().splitlines()
    if not lines:
        raise ValueError("no output")
    return json.loads(lines[-1])


def error_text(res: subprocess.CompletedProcess) -> str:
    return (res.stderr.strip().splitlines() or ["rc=%d" % res.returncode])[-1][:120]


def version(text) -> tuple[int, ...]:
    """'1.3.0' -> (1, 3, 0); missing or unreadable -> () (older than every release)."""
    nums = re.findall(r"\d+", text) if isinstance(text, str) else []
    return tuple(int(n) for n in nums[:3])
