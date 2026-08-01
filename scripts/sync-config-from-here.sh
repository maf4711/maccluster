#!/usr/bin/env bash
# Print commands to copy THIS machine's cluster.toml to peers via scp.
# Usage: ./scripts/sync-config-from-here.sh user@host1 user@host2 ...
set -euo pipefail
CFG="${MACCLUSTER_CONFIG:-$HOME/.config/maccluster/cluster.toml}"
if [[ ! -f "$CFG" ]]; then
  echo "missing $CFG — run maccluster init first" >&2
  exit 1
fi
if [[ $# -lt 1 ]]; then
  echo "usage: $0 user@peer1 [user@peer2 ...]" >&2
  echo "example: $0 a321@10.42.0.2 a321@10.42.0.3" >&2
  exit 2
fi
for host in "$@"; do
  echo "scp \"$CFG\" ${host}:.config/maccluster/cluster.toml"
  scp "$CFG" "${host}:.config/maccluster/cluster.toml" || true
  ssh "$host" 'chmod 600 ~/.config/maccluster/cluster.toml && maccluster config validate && sudo maccluster up && maccluster service install' || true
done
