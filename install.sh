#!/usr/bin/env bash
# MacCluster installer — works from a local checkout OR one-liner from GitHub.
#
# One-liner (raw.githubusercontent.com):
#   curl -fsSL https://raw.githubusercontent.com/maf4711/maccluster/main/install.sh | bash
#
# Zip download (no git required):
#   curl -fsSL https://github.com/maf4711/maccluster/archive/refs/heads/main.zip -o maccluster.zip
#   unzip maccluster.zip && cd maccluster-main && ./install.sh
#
set -euo pipefail
export PIP_NO_INPUT=1 PIP_DISABLE_PIP_VERSION_CHECK=1 GIT_TERMINAL_PROMPT=0

REPO_SLUG="${MACCLUSTER_REPO:-maf4711/maccluster}"
BRANCH="${MACCLUSTER_BRANCH:-main}"
ARCHIVE_URL="https://github.com/${REPO_SLUG}/archive/refs/heads/${BRANCH}.zip"
CLONE_URL="https://github.com/${REPO_SLUG}.git"

have() { command -v "$1" >/dev/null 2>&1; }

die() { echo "error: $*" >&2; exit 1; }

resolve_root() {
  # If this script lives inside a checkout with pyproject.toml, use that.
  if [[ -n "${BASH_SOURCE[0]:-}" && -f "${BASH_SOURCE[0]}" ]]; then
    local here
    here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    if [[ -f "${here}/pyproject.toml" ]]; then
      echo "${here}"
      return 0
    fi
  fi
  return 1
}

fetch_to_tmpdir() {
  local tmp
  tmp="$(mktemp -d "${TMPDIR:-/tmp}/maccluster-install.XXXXXX")"
  echo "Downloading ${ARCHIVE_URL} ..." >&2
  if have curl; then
    curl -fsSL --connect-timeout 15 --max-time 120 --retry 2 "${ARCHIVE_URL}" -o "${tmp}/maccluster.zip"
  elif have wget; then
    wget -qO "${tmp}/maccluster.zip" "${ARCHIVE_URL}"
  else
    die "need curl or wget to download archive"
  fi
  if have unzip; then
    unzip -q "${tmp}/maccluster.zip" -d "${tmp}"
  else
    # Python stdlib zipfile as fallback
    python3 - <<PY
import zipfile
from pathlib import Path
z = Path("${tmp}/maccluster.zip")
with zipfile.ZipFile(z) as f:
    f.extractall("${tmp}")
PY
  fi
  # GitHub archive extracts as <repo>-<branch>
  local dir
  dir="$(find "${tmp}" -maxdepth 1 -type d -name 'maccluster-*' | head -1)"
  [[ -n "${dir}" && -f "${dir}/pyproject.toml" ]] || die "archive layout unexpected"
  echo "${dir}"
}

clone_if_needed() {
  local tmp
  tmp="$(mktemp -d "${TMPDIR:-/tmp}/maccluster-git.XXXXXX")"
  echo "Cloning ${CLONE_URL} (${BRANCH}) ..." >&2
  git clone --depth 1 --branch "${BRANCH}" "${CLONE_URL}" "${tmp}/maccluster"
  echo "${tmp}/maccluster"
}

install_package() {
  local root="$1"
  cd "${root}"
  if have pipx; then
    echo "Installing with pipx from ${root} ..."
    pipx install --force .
  elif have python3; then
    local venv_path="${HOME}/.local/share/maccluster/venv"
    echo "pipx not found; installing into ${venv_path} ..."
    python3 -m venv "${venv_path}"
    "${venv_path}/bin/python" -m pip install --no-input --upgrade --force-reinstall .
    mkdir -p "${HOME}/.local/bin"
    ln -sfn "${venv_path}/bin/maccluster" "${HOME}/.local/bin/maccluster"
  else
    die "need pipx or python3"
  fi
}

main() {
  local automate=0 schedule=0 interval=86400 config_path="" automation_source="${ARCHIVE_URL}"
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --automate) automate=1; shift ;;
      --schedule) automate=1; schedule=1; shift ;;
      --interval|--config|--source)
        [[ $# -ge 2 ]] || die "$1 requires a value"
        case "$1" in
          --interval) interval="$2" ;;
          --config) config_path="$2" ;;
          --source) automation_source="$2" ;;
        esac
        shift 2 ;;
      --help|-h)
        echo "usage: install.sh [--automate] [--schedule] [--interval SEC] [--config PATH] [--source PATH_OR_URL]"
        echo "--automate runs cluster maintenance; --schedule also enables recurring maintenance."
        return 0 ;;
      *) die "unknown option: $1" ;;
    esac
  done
  [[ "$interval" =~ ^[0-9]+$ ]] && [[ "$interval" -ge 300 ]] || die "interval must be >= 300 seconds"
  local cli_options=()
  if [[ -n "$config_path" ]]; then cli_options=(--config "$config_path"); fi
  local root
  if root="$(resolve_root)"; then
    echo "Local checkout: ${root}"
  elif have git; then
    root="$(clone_if_needed)"
  else
    root="$(fetch_to_tmpdir)"
  fi

  install_package "${root}"
  export PATH="${HOME}/.local/bin:${PATH}"
  hash -r 2>/dev/null || true

  if have maccluster; then
    maccluster --version
    if [[ "$automate" == 1 ]]; then
      maccluster ${cli_options[@]:+"${cli_options[@]}"} config validate
      # Register independently: transient peer failures must not prevent retries.
      if [[ "$schedule" == 1 ]]; then
        maccluster ${cli_options[@]:+"${cli_options[@]}"} automation install --interval "$interval" --source "$automation_source"
      fi
      maccluster ${cli_options[@]:+"${cli_options[@]}"} automation run --source "$automation_source"
    fi
    echo "Done. Try: maccluster --help"
    echo "Config:    maccluster init"
    echo "Docs:      https://github.com/${REPO_SLUG}#readme"
  else
    die "installed entry point not found; check ~/.local/bin"
  fi
}

main "$@"
