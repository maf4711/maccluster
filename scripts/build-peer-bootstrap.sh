#!/usr/bin/env bash
# Build AirDrop/USB bootstrap package for a peer that cannot yet SSH over TB.
# Plants this Mac's SSH pubkey so later `maccluster remote-install` works on bridge only.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="${1:-$HOME/Desktop/maccluster-peer-install}"
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"

rm -rf "${OUT}"
mkdir -p "${OUT}/wheels"

echo "==> wheel"
python3 -m pip wheel -w "${OUT}/wheels" --no-deps "${ROOT}" >/dev/null
WHEEL="$(ls "${OUT}/wheels"/maccluster-*.whl | head -1)"
echo "    ${WHEEL}"

echo "==> config + pubkey"
CFG="${MACCLUSTER_CONFIG:-$HOME/.config/maccluster/cluster.toml}"
cp "${CFG}" "${OUT}/cluster.toml"
PUB=""
for c in "$HOME/.ssh/id_ed25519.pub" "$HOME/.ssh/id_rsa.pub"; do
  if [[ -f "$c" ]]; then PUB="$c"; break; fi
done
if [[ -z "${PUB}" ]]; then
  echo "error: no SSH public key" >&2
  exit 1
fi
cp "${PUB}" "${OUT}/cluster.pub"
cp "${ROOT}/scripts/peer-bootstrap-local.sh" "${OUT}/" 2>/dev/null || true

cat > "${OUT}/INSTALL-ON-PEER.sh" <<'EOF'
#!/usr/bin/env bash
# Run ON the peer Mac (Terminal), after AirDrop of this folder.
# Sets TB bridge IP, installs maccluster, plants SSH key for remote-install over bridge only.
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"

echo "==> pipx"
if ! command -v pipx >/dev/null 2>&1; then
  if command -v brew >/dev/null 2>&1; then
    brew install pipx && pipx ensurepath || true
  else
    python3 -m pip install --user pipx
    python3 -m pipx ensurepath || true
  fi
  export PATH="$HOME/.local/bin:$PATH"
fi

WHEEL="$(ls "$DIR"/wheels/maccluster-*.whl | head -1)"
echo "==> install $WHEEL"
pipx install --force "$WHEEL"
export PATH="$HOME/.local/bin:$PATH"
hash -r
maccluster --version

echo "==> plant SSH key (for remote-install over TB bridge)"
mkdir -p "$HOME/.ssh"
chmod 700 "$HOME/.ssh"
touch "$HOME/.ssh/authorized_keys"
chmod 600 "$HOME/.ssh/authorized_keys"
KEY="$(cat "$DIR/cluster.pub")"
if ! grep -qF "$(echo "$KEY" | awk '{print $2}')" "$HOME/.ssh/authorized_keys" 2>/dev/null; then
  echo "$KEY" >> "$HOME/.ssh/authorized_keys"
  echo "    authorized_keys updated"
else
  echo "    pubkey already present"
fi

echo "==> enable Remote Login if needed (may ask password)"
if command -v systemsetup >/dev/null 2>&1; then
  sudo systemsetup -setremotelogin on 2>/dev/null || true
fi

echo "==> cluster config + bridge up (TB only)"
mkdir -p "$HOME/.config/maccluster"
cp "$DIR/cluster.toml" "$HOME/.config/maccluster/cluster.toml"
chmod 600 "$HOME/.config/maccluster/cluster.toml"
maccluster config validate
sudo maccluster up
maccluster service install
maccluster ssh-config || true
maccluster doctor
maccluster status
echo ""
echo "Peer ready. From node-a over TB bridge only:"
echo "  maccluster remote-install node-b"
echo "  maccluster sync home --dry-run"
EOF
chmod +x "${OUT}/INSTALL-ON-PEER.sh"

cat > "${OUT}/README.txt" <<EOF
MacCluster peer bootstrap (offline)
===================================
1. AirDrop this folder or the .zip to the peer Mac mini.
2. On the peer Terminal:
     cd ~/Downloads/maccluster-peer-install   # or Desktop
     bash INSTALL-ON-PEER.sh
3. After that, TB IP (10.42.0.x) + SSH key are set.
4. On node-a (this studio machine), ONLY use the bridge:
     maccluster ssh-config
     maccluster remote-install node-b
     maccluster status

Wi‑Fi (192.168.x) is NOT used for cluster mesh or remote-install.
EOF

ZIP="${OUT}.zip"
rm -f "${ZIP}"
(cd "$(dirname "${OUT}")" && zip -qr "$(basename "${ZIP}")" "$(basename "${OUT}")")
echo "==> ${ZIP}"
ls -la "${ZIP}"
