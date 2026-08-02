# macOS Keychain — local MacCluster config

MacCluster can store **cluster.toml** and the **SSH peer user** (optional password)
in the **login Keychain** on **this Mac**.

**Scope: this Mac only.** The `security` CLI has no option to create
iCloud-synchronizable items, so pushed items land in the local
`login.keychain-db` and do **not** sync to a peer — even with iCloud Keychain
on and the same Apple ID.

To put config on another node:

```bash
maccluster keychain push-peer node-b --force
# or full install:
maccluster remote-install node-b
```

The Keychain is a **per-Mac** store: local backup of the config plus the SSH
user/password, so commands do not have to guess `$USER`.

## Service names

| Service | Content |
|---------|---------|
| `ai.maccluster.cluster-config` | Full `cluster.toml` text |
| `ai.maccluster.ssh.user` | SSH user (e.g. `mafoe`) |
| `ai.maccluster.ssh.password` | Optional bootstrap password (never printed by CLI) |

Account label default: `default`. Both forms work:

```bash
maccluster keychain --account default show
maccluster keychain show --account default
```

## Commands

```bash
# On primary Mac (after cluster.toml is correct):
maccluster keychain push --ssh-user mafoe
# optional: --ssh-password '…'   # only if you want bootstrap password in Keychain

maccluster keychain show

# Put config on peer over TB bridge (plants file; tries peer Keychain push):
maccluster keychain push-peer node-b --force
# or by IP:
maccluster keychain push-peer 10.42.0.2 --force --user mafoe

# On the peer Mac (GUI session / unlocked keychain):
maccluster init                 # disk exists → keep; empty → Keychain then template
# or:
maccluster keychain pull --force
maccluster config validate
sudo maccluster up
```

## `init` order

1. **Keychain peek** — what is stored locally  
2. If **disk config already exists** and not `--force` → keep disk (no error)  
3. If Keychain has config and disk empty (or `--force`) → **pull to disk**  
4. Else write **template** and **push to local Keychain**

```bash
maccluster init --force          # overwrite disk from Keychain if present, else template
maccluster init --no-keychain    # local only
```

## Peer access

| Setup | Peer sees Keychain items? |
|-------|---------------------------|
| Any Apple ID / iCloud Keychain state | **No** — `security` items are not synchronizable |
| `maccluster keychain push-peer <peer>` | Plants `cluster.toml` over TB SSH; tries remote `keychain push` |
| `maccluster remote-install <peer>` | Wheel + config + optional `up` |

## Writes need a local login session

`keychain push` writes to the login keychain. Over SSH that keychain is often
locked and the write fails with *"The authorization was denied."*  
`push-peer` still plants the **file** on the peer; for Keychain on the peer,
run in a Terminal **on that Mac**, or unlock first:

```bash
security unlock-keychain ~/Library/Keychains/login.keychain-db
maccluster keychain push
```

## Security

- Password is only in Keychain, never in `cluster.toml` or git.  
- `keychain show` prints `ssh_password: set|not set`, never the secret.  
- Change passwords that were typed in chat.
