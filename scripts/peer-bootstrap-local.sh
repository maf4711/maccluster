#!/usr/bin/env bash
# Run THIS script ON the peer Mac mini (node-b), not over SSH.
# Use when ssh-copy-id fails with "Connection closed" after password.
#
# How to get it onto the peer:
#   - AirDrop this file, or
#   - From node-a (after temporary file share / USB):
#       open smb://…  or copy via Finder Thunderbolt target disk mode
#   - Or paste the one-liner at the bottom into Terminal on the peer.
set -euo pipefail

export PATH="${HOME}/.local/bin:/opt/homebrew/bin:/usr/local/bin:${PATH}"

echo "==> MacCluster peer bootstrap (local)"
echo "host=$(scutil --get ComputerName 2>/dev/null || hostname)"
echo "user=$(whoami)"

echo "==> 1) Install CLI"
if command -v curl >/dev/null 2>&1; then
  curl -fsSL https://raw.githubusercontent.com/maf4711/maccluster/main/install.sh | bash
else
  echo "curl missing" >&2
  exit 1
fi
hash -r || true
maccluster --version

echo "==> 2) Config"
mkdir -p "${HOME}/.config/maccluster"
CFG="${HOME}/.config/maccluster/cluster.toml"
if [[ ! -f "$CFG" ]]; then
  maccluster init --force
  echo "Wrote template $CFG — edit hostnames/hw_uuid/IPs to match node-a, then re-run."
  echo "Or replace with the studio-live.toml from node-a."
fi

# If a shared live config was placed next to this script, use it
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -f "${SCRIPT_DIR}/../examples/studio-live.toml" ]]; then
  cp "${SCRIPT_DIR}/../examples/studio-live.toml" "$CFG"
  chmod 600 "$CFG"
  echo "Installed studio-live.toml"
elif [[ -f "${HOME}/Downloads/cluster.toml" ]]; then
  cp "${HOME}/Downloads/cluster.toml" "$CFG"
  chmod 600 "$CFG"
  echo "Installed ~/Downloads/cluster.toml"
fi

maccluster config validate || true

echo "==> 3) Bridge IP (admin password)"
if sudo -n true 2>/dev/null; then
  sudo maccluster up
else
  sudo maccluster up
fi

echo "==> 4) Heal service"
maccluster service install
maccluster doctor
maccluster status

echo "==> 5) Enable Remote Login for future SSH (optional)"
echo "System Settings → General → Sharing → Remote Login → ON"
echo "Allow full access for your user. Then from node-a:"
echo "  ssh-copy-id -i ~/.ssh/id_ed25519.pub $(whoami)@10.42.0.2"

echo "DONE peer bootstrap"
