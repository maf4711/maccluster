"""Noninteractive peer bootstrap script, transported alongside one wheel."""

import shlex

# Bootstrap cannot import maccluster: it may not be installed yet. Keep this
# protocol aligned with FileLock and automation_service._locked.
_LOCK_AND_EXEC = r"""
import fcntl, os, stat, sys, time
from pathlib import Path

path = Path.home() / '.config/maccluster/automation.lock'
path.parent.mkdir(parents=True, exist_ok=True)
try:
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        raise ValueError('installation lock must be a regular file')
    deadline = time.monotonic() + 1.0
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except BlockingIOError:
            if time.monotonic() >= deadline:
                raise ValueError('installation lock in progress') from None
            time.sleep(0.05)
    os.fchmod(fd, 0o600)
    os.ftruncate(fd, 0)
    os.write(fd, f'{os.getpid()}\n{time.time()}\n'.encode())
    os.set_inheritable(fd, True)
    os.execv('/bin/bash', ['/bin/bash', '-c', sys.argv[1], 'maccluster-install', *sys.argv[2:]])
except (OSError, ValueError) as exc:
    print(f'remote install: {exc}', file=sys.stderr)
    sys.exit(1)
"""


def _locked_script(body: str) -> str:
    # exec preserves the locked descriptor across Python -> bash, without a
    # waiting wrapper whose death could release a still-running child's lock.
    return (
        'export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"\n'
        + "exec python3 -c "
        + shlex.quote(_LOCK_AND_EXEC)
        + " "
        + shlex.quote(body)
        + ' "$@"\n'
    )


REMOTE_INSTALL_SH = _locked_script(r"""
set -euo pipefail
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
WHEEL="$1"
CFG="$2"
PUBKEY="$3"
DIGEST="$4"
PRESERVE_CONFIG="$5"
export PIP_NO_INPUT=1 GIT_TERMINAL_PROMPT=0 PIP_DISABLE_PIP_VERSION_CHECK=1

echo "==> args wheel=$WHEEL cfg=$CFG pubkey=$PUBKEY"
test -f "$WHEEL" || { echo "missing wheel"; exit 1; }
if [[ "$CFG" != "-" ]]; then test -f "$CFG" || { echo "missing config"; exit 1; }; fi
test -f "$PUBKEY" || { echo "missing pubkey"; exit 1; }

echo "==> verify wheel"
ACTUAL_DIGEST="$(/usr/bin/shasum -a 256 "$WHEEL" | /usr/bin/awk '{print $1}')"
[[ "$ACTUAL_DIGEST" == "$DIGEST" ]] || { echo "wheel checksum mismatch"; exit 1; }
WHEEL_NAME="${WHEEL##*/}"
EXPECTED_VERSION="${WHEEL_NAME#maccluster-}"
EXPECTED_VERSION="${EXPECTED_VERSION%%-*}"
STATE_DIR="$HOME/Library/Caches/maccluster"
mkdir -p "$STATE_DIR"
STAMP="$STATE_DIR/installed-wheel.sha256"
installed_digest() {
  local cli_dir
  cli_dir="$(python3 -c 'import os,sys; print(os.path.dirname(os.path.realpath(sys.argv[1])))' "$(command -v maccluster)")" || return 1
  "$cli_dir/python" -c 'import importlib.metadata,json; d=json.loads(importlib.metadata.distribution("maccluster").read_text("direct_url.json") or "{}"); a=d.get("archive_info",{}); print(a.get("hashes",{}).get("sha256") or a.get("hash","").removeprefix("sha256="))'
}
if [[ -f "$STAMP" ]] && [[ "$(cat "$STAMP")" == "$DIGEST" ]] &&
   [[ "$(maccluster --version 2>/dev/null || true)" == "maccluster $EXPECTED_VERSION" ]] &&
   [[ "$(installed_digest 2>/dev/null || true)" == "$DIGEST" ]]; then
  echo "package unchanged"
else
  echo "==> install $WHEEL"
  if command -v pipx >/dev/null 2>&1; then
    pipx install --force "$WHEEL"
  else
    VENV="$HOME/.local/share/maccluster/venv"
    python3 -m venv "$VENV"
    "$VENV/bin/python" -m pip install --no-input --upgrade --force-reinstall "$WHEEL"
    mkdir -p "$HOME/.local/bin"
    ln -sfn "$VENV/bin/maccluster" "$HOME/.local/bin/maccluster"
  fi
  hash -r || true
  [[ "$(maccluster --version)" == "maccluster $EXPECTED_VERSION" ]] || { echo "installed version mismatch"; exit 1; }
  STAMP_TMP="$(mktemp "$STATE_DIR/.installed-wheel.XXXXXX")"
  printf '%s\n' "$DIGEST" > "$STAMP_TMP"
  mv -f "$STAMP_TMP" "$STAMP"
fi

echo "==> plant SSH pubkey for cluster remote-install"
mkdir -p "$HOME/.ssh"
/bin/chmod 700 "$HOME/.ssh"
touch "$HOME/.ssh/authorized_keys"
/bin/chmod 600 "$HOME/.ssh/authorized_keys"
if ! /usr/bin/grep -qF "$(/usr/bin/awk '{print $2}' "$PUBKEY")" "$HOME/.ssh/authorized_keys" 2>/dev/null; then
  /bin/cat "$PUBKEY" >> "$HOME/.ssh/authorized_keys"
  echo "authorized_keys updated"
else
  echo "pubkey already present"
fi

echo "==> cluster config"
CONFIG_DEST="$HOME/.config/maccluster/cluster.toml"
if [[ "$CFG" != "-" ]] && { [[ "$PRESERVE_CONFIG" != "1" ]] || [[ ! -f "$CONFIG_DEST" ]]; }; then
  [[ ! -L "$CONFIG_DEST" ]] || { echo "refusing config symlink"; exit 1; }
  mkdir -p "$HOME/.config/maccluster"
  if [[ -f "$CONFIG_DEST" ]]; then /bin/cp -p "$CONFIG_DEST" "$CONFIG_DEST.bak"; fi
  /bin/cp "$CFG" "$CONFIG_DEST"
  /bin/chmod 600 "$CONFIG_DEST"
fi
maccluster config validate

echo "==> bridge up (TB only) + heal service"
RESULT=0
HEAL_RC=0
maccluster heal || HEAL_RC=$?
if [[ "$HEAL_RC" == "1" ]]; then
  HEAL_RC=0
  sudo -n "$(command -v maccluster)" heal || HEAL_RC=$?
fi
if [[ "$HEAL_RC" != "0" ]]; then RESULT=1; fi
maccluster service install || RESULT=1
maccluster doctor || RESULT=1
maccluster status || RESULT=1
echo "remote install complete on $(hostname), status=$RESULT"
exit "$RESULT"
""")
