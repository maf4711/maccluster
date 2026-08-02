#!/usr/bin/env bash
# Install MacCluster on a peer — TB bridge only (10.42.0.0/24).
#
# Usage:
#   ./scripts/remote-install.sh node-b
#   ./scripts/remote-install.sh a321@10.42.0.2
#   ./scripts/remote-install.sh 10.42.0.2 --copy-config
#
# Refuses Wi‑Fi/LAN IPs (e.g. 192.168.x). Cluster traffic must use bridge0.
# Prefers: maccluster remote-install (same policy, builds wheel locally).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"

PEER="${1:-}"
shift || true
EXTRA=()
while [[ $# -gt 0 ]]; do
  EXTRA+=("$1")
  shift
done

if [[ -z "${PEER}" ]]; then
  echo "usage: $0 <node-id|10.42.0.x|user@10.42.0.x> [--dry-run]" >&2
  exit 2
fi

# Strip user@ for policy check; maccluster CLI takes node id or IP
TARGET_IP="${PEER##*@}"
if [[ "${TARGET_IP}" == node-* ]]; then
  :
elif [[ "${TARGET_IP}" =~ ^10\.42\.0\.[0-9]+$ ]]; then
  :
else
  echo "error: peer must be cluster id or 10.42.0.x (TB bridge only), got: ${PEER}" >&2
  echo "       Wi‑Fi/LAN (e.g. 192.168.x) is not used for cluster remote-install." >&2
  exit 2
fi

# Prefer installed CLI
if command -v maccluster >/dev/null 2>&1; then
  # map user@ip → ip for CLI
  ARG="${TARGET_IP}"
  if [[ "${PEER}" == node-* ]]; then
    ARG="${PEER}"
  fi
  exec maccluster remote-install "${ARG}" ${EXTRA[@]:+"${EXTRA[@]}"}
fi

# Fallback: python from checkout
export MACCLUSTER_SRC="${ROOT}"
cd "${ROOT}"
python3 -m maccluster remote-install "${TARGET_IP}" ${EXTRA[@]:+"${EXTRA[@]}"}
