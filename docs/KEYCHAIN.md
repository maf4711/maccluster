# macOS Keychain — shared MacCluster config

MacCluster can store **cluster.toml** and the **SSH peer user** (optional password)
in the **login Keychain**.

With **iCloud Keychain** enabled and the **same Apple ID** on node-a and node-b,
those items can sync so the peer sees them and can `init` / `keychain pull` without AirDrop.

## Service names

| Service | Content |
|---------|---------|
| `ai.maccluster.cluster-config` | Full `cluster.toml` text |
| `ai.maccluster.ssh.user` | SSH user (e.g. `mafoe`) |
| `ai.maccluster.ssh.password` | Optional bootstrap password (never printed by CLI) |

Account label default: `default`.

## Commands

```bash
# On primary Mac (after cluster.toml is correct):
maccluster keychain push --ssh-user mafoe
# optional: --ssh-password '…'   # only if you want bootstrap password in Keychain

maccluster keychain show

# On peer Mac (same Apple ID, after Keychain sync):
maccluster init                 # checks Keychain first → writes cluster.toml
# or:
maccluster keychain pull --force
maccluster config validate
sudo maccluster up
```

## `init` order

1. **Keychain check** — print what is stored  
2. If config present → **pull to disk**  
3. Else write **template** and **push to Keychain**

```bash
maccluster init --force          # allow overwrite from Keychain / template
maccluster init --no-keychain    # local only
```

## Peer access

| Setup | Peer sees Keychain items? |
|-------|---------------------------|
| Same Apple ID + iCloud Keychain on | Yes (after sync) |
| Different Apple ID | No — use AirDrop zip or `keychain push` on each Mac |
| Local Keychain only | Only on that Mac |

## Security

- Password is only in Keychain, never in `cluster.toml` or git.  
- `keychain show` prints `ssh_password: set|not set`, never the secret.  
- Change passwords that were typed in chat.
