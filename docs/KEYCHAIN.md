# macOS Keychain — shared MacCluster config

MacCluster can store **cluster.toml** and the **SSH peer user** (optional password)
in the **login Keychain**.

**Scope: this Mac only.** The `security` CLI has no option to create
iCloud-synchronizable items, so pushed items land in the local
`login.keychain-db` and do **not** sync to a peer — even with iCloud Keychain
on and the same Apple ID. Use `maccluster remote-install <peer>` to put the
config on a peer (it copies `cluster.toml` over the TB bridge).

The Keychain is therefore a **per-Mac** store: a local backup of the config
plus the SSH user/password, so commands do not have to guess `$USER`.

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

# On the peer Mac (after its own `keychain push`, or after remote-install):
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
| Any Apple ID / iCloud Keychain state | **No** — `security` items are not synchronizable |
| Getting config to a peer | `maccluster remote-install <peer>` (TB bridge), then `keychain push` there |

## Writes need a local login session

`keychain push` writes to the login keychain. Over SSH that keychain is locked
and the write fails with *"The authorization was denied."* Run `keychain push`
in a Terminal **on that Mac**, or unlock first:

```bash
security unlock-keychain ~/Library/Keychains/login.keychain-db
```

## Security

- Password is only in Keychain, never in `cluster.toml` or git.  
- `keychain show` prints `ssh_password: set|not set`, never the secret.  
- Change passwords that were typed in chat.
