# Remote install — TB bridge only

MacCluster control-plane SSH (**remote-install**, **sync home**) uses **only** the
Thunderbolt bridge subnet (`10.42.0.0/24` by default).

| Allowed | Forbidden |
|---------|-----------|
| `10.42.0.1` ↔ `10.42.0.2` on `bridge0` | Wi‑Fi / LAN (`192.168.x`, etc.) |
| `BindAddress` = Self cluster IP | Default route via en0/Wi‑Fi for peer ops |

## One-time peer bootstrap (no SSH yet)

If the peer has no key login and no MacCluster:

```bash
# on node-a
cd ~/Developer/fabrik/projects/maccluster
./scripts/build-peer-bootstrap.sh
# → ~/Desktop/maccluster-peer-install.zip
```

AirDrop the zip to the peer, then on the **peer**:

```bash
unzip ~/Downloads/maccluster-peer-install.zip
cd maccluster-peer-install
bash INSTALL-ON-PEER.sh
```

That installs the wheel, plants **this Mac’s SSH pubkey**, runs `sudo maccluster up`
(bridge Self-IP), and starts the heal service.

## From then on (node-a, bridge only)

```bash
export PATH="$HOME/.local/bin:$PATH"
maccluster ssh-config                 # ~/.ssh/config.d/maccluster BindAddress
maccluster remote-install node-b      # wheel + config over TB SSH
maccluster remote-install node-b --dry-run
maccluster status
maccluster sync home --dry-run
```

`maccluster remote-install 192.168.178.127` **refuses** non-cluster IPs.

## Scripts

| Script / command | Role |
|------------------|------|
| `maccluster ssh-config` | OpenSSH fragment: `Host 10.42.0.*` → `BindAddress` Self-IP |
| `maccluster remote-install <peer>` | Offline wheel install over bridge SSH |
| `./scripts/remote-install.sh` | Thin wrapper → CLI |
| `./scripts/build-peer-bootstrap.sh` | AirDrop package + pubkey |

## Requirements

1. TB cable + `sudo maccluster up` on both sides (fixed `10.42.0.x`)
2. SSH key in peer `authorized_keys` (bootstrap plants it)
3. Remote Login on peer
