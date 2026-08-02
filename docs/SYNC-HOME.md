# `maccluster sync home` — Apple ditto, newest-wins over Thunderbolt

MacCluster **bring-up** (`up` / `heal` / `status`) only manages the TB mesh IP stack.
It does **not** clone disks. Home file sync is an explicit CLI layer:

```bash
maccluster sync home              # all peers in cluster.toml
maccluster sync home --dry-run    # preview transfers
maccluster sync home --peer node-b
```

## Why Apple `ditto` (not Homebrew rsync)

| Tool | Role |
|------|------|
| **`/usr/bin/ditto`** | Apple system copy — preserves **resource forks, extended attributes, ACLs, quarantine** by default (installer-grade fidelity on APFS) |
| **`ssh` / `scp`** | Transport over TB fixed IPs (`10.42.0.x`) |
| mtime compare | **Newest wins** per file (equal mtime → skip) |
| Deletes | **Never** |

Apple’s cloud product for Home is **iCloud Drive** (Desktop & Documents). That needs an
Apple ID and internet and is **not** TB-local. MacCluster keeps data on the mesh for
offline / zero-cloud operation.

## How a sync works

Per peer:

1. **SSH preflight** (BatchMode, key-only)
2. **Inventory** local + remote (`find`-style walk, with excludes)
3. **Plan** — push if local missing on remote or local `mtime` newer; pull the inverse
4. **Push** — hardlink-stage → `ditto -c` (CPIO) → `scp` → remote `ditto -x` into Home
5. **Pull** — same in reverse

## Prerequisites

1. `~/.config/maccluster/cluster.toml` with peers
2. TB mesh up (`sudo maccluster up`, `maccluster status`)
3. **SSH key login** (no password prompts):

   ```bash
   ssh-copy-id a321@10.42.0.2
   ssh a321@10.42.0.2 /usr/bin/true
   ```

   See [PEER-SSH.md](./PEER-SSH.md) if auth closes after accepting the key.

4. Remote Login on; `/usr/bin/ditto` and `/usr/bin/python3` on both Macs (stock macOS)

## Usage

```bash
maccluster sync home --dry-run -v
maccluster sync home
maccluster sync home --peer 10.42.0.2
maccluster sync home --push-only
maccluster sync home --exclude 'Movies/' --exclude 'Downloads/Large/'
maccluster --json sync home --dry-run
```

| Flag | Meaning |
|------|---------|
| `--dry-run` | Plan only (no archives, no writes) |
| `--peer ID\|IP` | Single peer |
| `--push-only` / `--pull-only` | One direction |
| `--user NAME` | SSH user (default `$USER`) |
| `--home` / `--remote-home` | Override paths (default `~`) |
| `--exclude PATTERN` | Extra exclude (repeatable) |
| `--timeout SEC` | Budget per heavy step (default 3600) |
| `--no-progress` | Disable live progress bar |

## Progress bar

On a TTY (not with `--json` / `--no-progress`), stderr shows a live line:

```text
[████████░░░░░░░░░░░░░░░░░░░░]  42.5%  push transfer  820 MB/1.9 GB  112 MB/s  ETA 10s  → a321@10.42.0.2:/tmp/…
```

| Field | Meaning |
|-------|---------|
| bar + % | Overall bytes planned (push+pull) |
| direction + phase | `push`/`pull` × `stage` / `archive` / `transfer` / `extract` |
| size | Done / total |
| rate | Live throughput (EMA) |
| ETA | Remaining time from rate |
| path | Current file or transfer target |

Before the bar, a short plan lists sample paths and sizes.

## Default excludes

Trash, Caches, Xcode DerivedData, CoreSimulator, npm cacache, `node_modules`,
virtualenvs, `.DS_Store` — see `SYNC_HOME_EXCLUDES` in `src/maccluster/constants.py`.

## Exit codes

| Code | Meaning |
|------|---------|
| 0 | All peers OK |
| 1 | All peers failed (often SSH) |
| 2 | Usage / no peer matched |
| 3 | Partial |

## What this is not

- Not full-disk clone / CCC / `asr`
- Not continuous background sync (run manually or your own LaunchAgent)
- Not iCloud (use System Settings → Apple ID if you want Apple’s cloud Home)
