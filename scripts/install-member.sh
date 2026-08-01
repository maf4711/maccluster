#!/usr/bin/env bash
# Install MacCluster on this Mac mini and prepare config/service.
# Run on EVERY cluster member (same repo path or copied tree).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

echo "==> Install maccluster (pipx preferred)"
if command -v pipx >/dev/null 2>&1; then
  pipx install --force .
else
  python3 -m pip install --user -e .
  export PATH="$HOME/.local/bin:$PATH"
fi
export PATH="$HOME/.local/bin:$PATH"
hash -r
maccluster --version

echo "==> Ensure config"
if [[ ! -f "${MACCLUSTER_CONFIG:-$HOME/.config/maccluster/cluster.toml}" ]]; then
  maccluster init
  echo "Edit ~/.config/maccluster/cluster.toml — set all 4 nodes hostnames/hw_uuid/IPs identically on every mini."
else
  maccluster config validate || true
fi

echo "==> Optional: iperf3 for bench"
if command -v brew >/dev/null 2>&1; then
  brew list iperf3 >/dev/null 2>&1 || brew install iperf3 || true
fi

echo "==> Bring-up (needs admin once)"
if sudo -n true 2>/dev/null; then
  sudo maccluster up
else
  echo "Run manually:  sudo maccluster up"
  echo "Or:            osascript -e 'do shell script \"$(command -v maccluster) up\" with administrator privileges'"
fi

echo "==> LaunchAgent heal loop"
maccluster service install
maccluster service status
maccluster doctor
maccluster status
echo "Done."
