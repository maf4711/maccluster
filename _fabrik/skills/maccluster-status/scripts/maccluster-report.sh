#!/usr/bin/env bash
# MacCluster report: history + current monitor snapshot.
# Used by skill maccluster-status.
set -uo pipefail

export PATH="${HOME}/.local/bin:/opt/homebrew/bin:/usr/local/bin:${PATH}"
export LC_ALL=C

REPO_CANDIDATES=(
  "${MACCLUSTER_REPO:-}"
  "${HOME}/Developer/fabrik/projects/maccluster"
  "${HOME}/Developer/maccluster"
)
REPO=""
for d in "${REPO_CANDIDATES[@]}"; do
  [[ -z "$d" ]] && continue
  if [[ -d "${d}/.git" ]] || [[ -f "${d}/pyproject.toml" ]]; then
    REPO="$d"
    break
  fi
done

CFG="${MACCLUSTER_CONFIG:-${HOME}/.config/maccluster/cluster.toml}"
CACHE="${HOME}/Library/Caches/maccluster/traffic_sample.json"
AUDIT="${HOME}/.local/state/maccluster/actions.log"
PLIST="${HOME}/Library/LaunchAgents/com.maccluster.heal.plist"
MC=""
if [[ -x "${HOME}/.local/bin/maccluster" ]]; then
  MC="${HOME}/.local/bin/maccluster"
elif command -v maccluster >/dev/null 2>&1; then
  MC="$(command -v maccluster)"
fi

hr() { printf '\n======== %s ========\n' "$1"; }

hr "MACCLUSTER REPORT"
date -u '+utc=%Y-%m-%dT%H:%M:%SZ'
echo "host=$(scutil --get ComputerName 2>/dev/null || hostname)"
echo "cli=${MC:-missing}"

hr "VERSION"
if [[ -n "$MC" ]]; then
  "$MC" --version 2>&1 || true
else
  echo "maccluster not on PATH — install:"
  echo '  curl -fsSL https://raw.githubusercontent.com/maf4711/maccluster/main/install.sh | bash'
fi

hr "LIVE DOCTOR"
if [[ -n "$MC" ]]; then
  "$MC" doctor 2>&1 || true
fi

hr "LIVE STATUS"
if [[ -n "$MC" ]]; then
  # One warm-up sample for rates, then human status (bounded time)
  "$MC" --json status >/dev/null 2>&1 || true
  sleep 0.8
  "$MC" status 2>&1 || true
fi

hr "THUNDERBOLT"
if [[ -n "$MC" ]]; then
  "$MC" tb 2>&1 | head -40 || true
fi

hr "TOPOLOGY"
if [[ -n "$MC" ]]; then
  "$MC" topo 2>&1 | head -30 || true
fi

hr "SERVICE"
if [[ -n "$MC" ]]; then
  "$MC" service status 2>&1 || true
fi
if [[ -f "$PLIST" ]]; then
  echo "plist=$PLIST"
  launchctl print "gui/$(id -u)/com.maccluster.heal" 2>&1 | head -12 || true
else
  echo "plist=missing"
fi

hr "CONFIG"
echo "path=$CFG"
if [[ -f "$CFG" ]]; then
  ls -la "$CFG" 2>/dev/null || true
  grep -E '^(schema_version|name|subnet|bridge|heal|ssh_|id |ip |hw_uuid)' "$CFG" 2>/dev/null \
    || head -40 "$CFG"
else
  echo "missing — run: maccluster init"
fi

hr "TRAFFIC CACHE"
if [[ -f "$CACHE" ]]; then
  ls -la "$CACHE" 2>/dev/null || true
else
  echo "no traffic cache yet"
fi

hr "AUDIT LOG (tail)"
if [[ -f "$AUDIT" ]]; then
  ls -la "$AUDIT" 2>/dev/null || true
  tail -n 20 "$AUDIT" 2>/dev/null || true
else
  echo "no audit log (default off)"
fi

hr "GIT HISTORY"
if [[ -n "$REPO" && -d "${REPO}/.git" ]]; then
  echo "repo=$REPO"
  git -C "$REPO" log --oneline -12 2>/dev/null || true
  git -C "$REPO" describe --tags --always 2>/dev/null || true
else
  echo "local git repo not found"
fi

hr "CHANGELOG"
if [[ -n "$REPO" && -f "${REPO}/CHANGELOG.md" ]]; then
  head -n 35 "${REPO}/CHANGELOG.md"
fi

hr "GITHUB RELEASES"
if command -v gh >/dev/null 2>&1; then
  gh release list -R maf4711/maccluster -L 5 2>/dev/null || echo "gh release list failed"
else
  echo "https://github.com/maf4711/maccluster/releases"
fi

hr "END"
exit 0
