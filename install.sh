#!/usr/bin/env bash
# MacCluster local install helper (pipx preferred, pip fallback).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

if command -v pipx >/dev/null 2>&1; then
  echo "Installing maccluster with pipx..."
  pipx install --force .
elif command -v python3 >/dev/null 2>&1; then
  echo "pipx not found; installing editable with python3 -m pip..."
  python3 -m pip install -e ".[dev]"
else
  echo "error: need pipx or python3" >&2
  exit 1
fi

echo "Done. Try: maccluster --help"
