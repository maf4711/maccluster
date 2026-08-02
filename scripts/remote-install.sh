#!/usr/bin/env bash
# Install MacCluster on a remote cluster member over SSH (TB IP preferred).
#
# Usage:
#   ./scripts/remote-install.sh a321@10.42.0.2
#   ./scripts/remote-install.sh a321@10.42.0.2 --copy-config
#
# Prerequisites: SSH key auth to the peer (ssh-copy-id user@host).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET="${1:-}"
COPY_CONFIG=0
if [[ "${2:-}" == "--copy-config" ]] || [[ "${1:-}" == "--copy-config" ]]; then
  COPY_CONFIG=1
fi
if [[ -z "${TARGET}" || "${TARGET}" == --* ]]; then
  echo "usage: $0 user@host [--copy-config]" >&2
  exit 2
fi

SSH=(ssh -o BatchMode=yes -o ConnectTimeout=8 -o StrictHostKeyChecking=accept-new)
SCP=(scp -o BatchMode=yes -o ConnectTimeout=8 -o StrictHostKeyChecking=accept-new)

echo "==> probe ${TARGET}"
"${SSH[@]}" "${TARGET}" 'echo ok host=$(hostname) user=$(whoami) arch=$(uname -m)'

echo "==> install maccluster via GitHub one-liner"
"${SSH[@]}" "${TARGET}" 'bash -s' <<'REMOTE'
set -euo pipefail
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
if ! command -v curl >/dev/null 2>&1; then
  echo "curl missing" >&2; exit 1
fi
# Prefer pipx
if ! command -v pipx >/dev/null 2>&1; then
  if command -v brew >/dev/null 2>&1; then
    brew install pipx || true
    pipx ensurepath || true
  fi
fi
curl -fsSL https://raw.githubusercontent.com/maf4711/maccluster/main/install.sh | bash
export PATH="$HOME/.local/bin:$PATH"
hash -r || true
maccluster --version
REMOTE

if [[ "${COPY_CONFIG}" == "1" ]]; then
  CFG="${MACCLUSTER_CONFIG:-$HOME/.config/maccluster/cluster.toml}"
  if [[ ! -f "${CFG}" ]]; then
    echo "local config missing: ${CFG}" >&2
    exit 1
  fi
  echo "==> copy config ${CFG}"
  "${SSH[@]}" "${TARGET}" 'mkdir -p ~/.config/maccluster'
  "${SCP[@]}" "${CFG}" "${TARGET}:.config/maccluster/cluster.toml"
  "${SSH[@]}" "${TARGET}" 'chmod 600 ~/.config/maccluster/cluster.toml && export PATH="$HOME/.local/bin:$PATH"; maccluster config validate'
fi

echo "==> bring-up + service (may prompt for remote sudo password if not passwordless)"
"${SSH[@]}" -t "${TARGET}" 'export PATH="$HOME/.local/bin:$PATH"; maccluster config validate || true; sudo -n maccluster up 2>/dev/null || sudo maccluster up; maccluster service install; maccluster doctor; maccluster status'

echo "Done remote install on ${TARGET}"
