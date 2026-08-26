#!/usr/bin/env bash
# One-shot: plant this Mac's SSH pubkey on a peer (password prompt once).
# Works around:
#   - macOS ssh-copy-id " -i must not be specified more than once"
#   - ~/.ssh/config.d/maccluster PasswordAuthentication no
#   - ControlMaster interference
#
# Usage:
#   ./scripts/peer-ssh-key-install.sh a321@10.42.0.2          # TB bridge (preferred)
#   ./scripts/peer-ssh-key-install.sh a321@192.168.178.127    # LAN fallback
set -euo pipefail

PEER="${1:-}"
if [[ -z "${PEER}" ]]; then
  echo "usage: $0 user@host" >&2
  echo "  preferred: $0 a321@10.42.0.2" >&2
  exit 2
fi

PUB="${HOME}/.ssh/id_ed25519.pub"
KEY="${HOME}/.ssh/id_ed25519"
if [[ ! -f "${PUB}" ]]; then
  echo "error: missing ${PUB}" >&2
  exit 1
fi
PUBLINE="$(tr -d '\n' < "${PUB}")"

HOST_PART="${PEER##*@}"
USER_PART="${PEER%%@*}"
if [[ "${USER_PART}" == "${PEER}" ]]; then
  USER_PART="${USER:-a321}"
  PEER="${USER_PART}@${HOST_PART}"
fi

# Build ssh options that IGNORE user config (-F /dev/null) so cluster PasswordAuthentication=no
# and ControlMaster do not break password bootstrap.
SSH_OPTS=(
  -F /dev/null
  -o StrictHostKeyChecking=accept-new
  -o UserKnownHostsFile="${HOME}/.ssh/known_hosts"
  -o ControlMaster=no
  -o ControlPath=none
  -o PreferredAuthentications=password,keyboard-interactive
  -o PubkeyAuthentication=no
  -o PasswordAuthentication=yes
  -o NumberOfPasswordPrompts=3
  -o IdentitiesOnly=yes
)

# Bind to TB Self-IP only when targeting cluster subnet
if [[ "${HOST_PART}" == 10.42.0.* ]]; then
  SELF_IP="$(ifconfig bridge0 2>/dev/null | awk '/inet /{print $2; exit}')"
  if [[ -n "${SELF_IP}" ]]; then
    SSH_OPTS+=(-o "BindAddress=${SELF_IP}" -b "${SELF_IP}")
    echo "Using TB bridge bind ${SELF_IP} → ${HOST_PART}"
  fi
fi

echo "=== Plant pubkey on ${PEER} ==="
echo "You will be asked once for the password of ${USER_PART} on the peer."
echo "Pubkey: ${PUBLINE:0:40}…"
echo ""

# Method 1: manual install via ssh (most reliable on macOS; avoids double -i bug)
REMOTE_CMD=$(cat <<EOF
set -e
mkdir -p "\$HOME/.ssh"
chmod 700 "\$HOME/.ssh"
touch "\$HOME/.ssh/authorized_keys"
chmod 600 "\$HOME/.ssh/authorized_keys"
if grep -qF '${PUBLINE}' "\$HOME/.ssh/authorized_keys" 2>/dev/null; then
  echo "pubkey already present"
else
  echo '${PUBLINE}' >> "\$HOME/.ssh/authorized_keys"
  echo "pubkey installed"
fi
# ensure Remote Login-friendly perms on home
chmod go-w "\$HOME" 2>/dev/null || true
EOF
)

if ! /usr/bin/ssh "${SSH_OPTS[@]}" "${PEER}" "${REMOTE_CMD}"; then
  echo "" >&2
  echo "FAILED: password login closed or rejected." >&2
  echo "On the peer Mac (physical screen), check:" >&2
  echo "  1) System Settings → General → Sharing → Remote Login = ON" >&2
  echo "  2) Allow access for user: ${USER_PART}" >&2
  echo "  3) Password is the Mac login password for ${USER_PART}" >&2
  echo "  4) Or AirDrop Desktop/maccluster-peer-install.zip → bash INSTALL-ON-PEER.sh" >&2
  exit 1
fi

echo ""
echo "=== Test key login (BatchMode, no password) ==="
TEST_OPTS=(
  -F /dev/null
  -o BatchMode=yes
  -o ConnectTimeout=8
  -o StrictHostKeyChecking=accept-new
  -o UserKnownHostsFile="${HOME}/.ssh/known_hosts"
  -o ControlMaster=no
  -o ControlPath=none
  -o IdentitiesOnly=yes
  -i "${KEY}"
  -o PreferredAuthentications=publickey
  -o PasswordAuthentication=no
)
if [[ "${HOST_PART}" == 10.42.0.* ]]; then
  SELF_IP="$(ifconfig bridge0 2>/dev/null | awk '/inet /{print $2; exit}')"
  if [[ -n "${SELF_IP}" ]]; then
    TEST_OPTS+=(-o "BindAddress=${SELF_IP}" -b "${SELF_IP}")
  fi
fi

if /usr/bin/ssh "${TEST_OPTS[@]}" "${PEER}" 'echo OK host=$(hostname) user=$(whoami)'; then
  echo ""
  echo "DONE. Next on this Mac:"
  echo "  export PATH=\"\$HOME/.local/bin:\$PATH\""
  echo "  maccluster remote-install node-b"
  echo "  maccluster sync home"
else
  echo "Key plant may have worked but BatchMode test failed." >&2
  echo "Retry: ssh -i ${KEY} ${PEER} true" >&2
  exit 1
fi
