# Peer SSH / Remote install troubleshooting

## What you saw

```text
(a321@10.42.0.2) Password:
Connection closed by 10.42.0.2 port 22
```

Means: TCP/SSH daemon is up, but **authentication failed or was aborted** on the peer.
Keys were **not** installed. Remote install cannot continue until SSH login works.

Local cluster can still be **healthy** (peer reachable via `tcp:22` for monitor, even without a login).

## Fix on the peer Mac (physical screen/keyboard)

1. **System Settings → General → Sharing → Remote Login**
   - Turn **ON**
   - Allow access for: **All users** or specifically user `a321`
2. Confirm the login password for user `a321` (same as unlock password)
3. Optional but helpful:
   ```bash
   # On peer Terminal
   sudo systemsetup -setremotelogin on
   sudo dscl . -append /Groups/com.apple.access_ssh GroupMembership a321
   ```
4. Retry from node-a:
   ```bash
   ssh a321@10.42.0.2
   # then:
   ssh-copy-id -i ~/.ssh/id_ed25519.pub a321@10.42.0.2
   ```

### If password always closes the connection

- Wrong username (not `a321` on that mini)
- Password wrong / account locked
- Remote Login only allows selected users (add `a321`)
- Screen Time / MDM restrictions
- Try another account that has admin + Remote Login

## Bootstrap without SSH (recommended if SSH fights you)

On **node-a**, put config where peer can get it:

```bash
cp ~/.config/maccluster/cluster.toml ~/Desktop/cluster.toml
# AirDrop Desktop/cluster.toml to the peer
```

On **peer** Terminal (one-liner):

```bash
curl -fsSL https://raw.githubusercontent.com/maf4711/maccluster/main/install.sh | bash
mkdir -p ~/.config/maccluster
# After AirDrop, or paste file:
cp ~/Downloads/cluster.toml ~/.config/maccluster/cluster.toml
chmod 600 ~/.config/maccluster/cluster.toml
export PATH="$HOME/.local/bin:$PATH"
maccluster config validate
sudo maccluster up
maccluster service install
maccluster status
```

Or copy `scripts/peer-bootstrap-local.sh` + `examples/studio-live.toml` via AirDrop and run:

```bash
bash peer-bootstrap-local.sh
```

## After SSH works

From node-a:

```bash
cd ~/Developer/fabrik/projects/maccluster
./scripts/remote-install.sh a321@10.42.0.2 --copy-config
maccluster status
maccluster doctor
```

## Note on ICMP

Ping to `10.42.0.2` may fail while TCP/SSH works (macOS often filters ICMP).
MacCluster ≥ 0.1.2 treats **TCP:22** as reachable (`via=tcp:22`).
