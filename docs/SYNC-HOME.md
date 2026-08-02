# `maccluster sync home` — CCC-inspired Home sync over Thunderbolt

MacCluster **bring-up** (`up` / `heal` / `status`) only manages the TB mesh IP stack.
It does **not** clone disks. Home file sync is an explicit CLI layer, inspired by
useful **Carbon Copy Cloner** ideas (compare, filters, SafetyNet, verify, schedule)
— but **two-way**, **Home-only**, **no deletes**, over TB/SSH with Apple `ditto`.

```bash
maccluster sync home --compare
maccluster sync home --dry-run
maccluster sync home --safetynet --verify
maccluster sync home --preset documents,developer --peer node-b
```

## Why Apple `ditto` (not Homebrew rsync)

| Tool | Role |
|------|------|
| **`/usr/bin/ditto`** | Apple system copy — resource forks, xattrs, ACLs, quarantine |
| **`ssh` / `scp`** | Transport over TB fixed IPs (`10.42.0.x`) |
| mtime / policy | Conflict resolution (default **newest wins**) |
| Deletes | **Never** |

Not iCloud Drive. Stays on the mesh for offline / zero-cloud operation.

## CCC feature map

| CCC idea | MacCluster |
|----------|------------|
| Compare / preview | `--compare`, `--dry-run` |
| Filters / task scope | `--preset`, `--include`, `--exclude`, `--exclude-from` |
| SafetyNet | `--safetynet` → `~/.maccluster-safetynet/<ts>/` |
| Post-clone verification | `--verify` / `--verify-sample N` |
| Task history | run logs + `sync home --last` |
| Scheduling | `service sync-install --interval SEC` |
| Preflight | SSH + free space (`--min-free`) + optional speedtest |
| Bandwidth batching | `--max-files` / `--max-bytes` |
| Conflict policy | `--conflict-policy newer\|larger\|prefer-local\|prefer-remote\|skip-conflict` |
| Quick update | `--quick` (files since last success) |
| APFS snapshot | `--apfs-snapshot` (`tmutil localsnapshot`, opt-in) |
| Notifications | `--notify` (Notification Center on fail) |
| Bootable clone / ASR | **Out of scope** |
| Destination prune/delete | **Never** (optional prune not offered) |

## How a sync works

Per peer:

1. **SSH preflight** (BatchMode, key-only) + optional free-space check  
2. **Inventory** local + remote (excludes / includes / presets)  
3. **Plan** by conflict policy  
4. Optional **SafetyNet** backup of local files about to be overwritten  
5. **Push** — hardlink-stage → `ditto -c` → `scp` → remote `ditto -x`  
6. **Pull** — same in reverse  
7. Optional **verify** sample + **run log** JSON  

## Prerequisites

1. `~/.config/maccluster/cluster.toml` with peers  
2. TB mesh up (`sudo maccluster up`, `maccluster status`)  
3. **SSH key login**:

   ```bash
   ssh-copy-id a321@10.42.0.2
   ssh a321@10.42.0.2 /usr/bin/true
   ```

   See [PEER-SSH.md](./PEER-SSH.md) if auth closes after accepting the key.

4. Remote Login on; stock `/usr/bin/ditto` and `/usr/bin/python3`

## Usage

```bash
maccluster sync home --compare -v
maccluster sync home --dry-run
maccluster sync home --safetynet --verify --notify
maccluster sync home --peer 10.42.0.2
maccluster sync home --push-only
maccluster sync home --preset documents,desktop,developer
maccluster sync home --include 'Projects/' --exclude 'Movies/'
maccluster sync home --exclude-from ~/.config/maccluster/sync-excludes
maccluster sync home --conflict-policy prefer-local
maccluster sync home --quick --max-files 500 --max-bytes 1073741824
maccluster sync home --min-free 5368709120
maccluster sync home --apfs-snapshot
maccluster sync home --last
maccluster --json sync home --compare
maccluster service sync-install --interval 3600
maccluster service sync-status
maccluster service sync-uninstall
```

### Flags

| Flag | Meaning |
|------|---------|
| `--compare` | Diff report only (no transfer) |
| `--dry-run` | Plan only (no archives, no writes) |
| `--last` | Print last run log |
| `--peer ID\|IP` | Single peer |
| `--push-only` / `--pull-only` | One direction |
| `--user NAME` | SSH user (default `$USER`) |
| `--home` / `--remote-home` | Override paths (default `~`) |
| `--exclude PATTERN` | Extra exclude (repeatable) |
| `--exclude-from FILE` | Exclude file (default `~/.config/maccluster/sync-excludes`) |
| `--preset NAME` | Include preset (repeatable/comma) |
| `--include PATH` | Only these roots under Home |
| `--conflict-policy` | `newer` (default), `larger`, `prefer-local`, `prefer-remote`, `skip-conflict` |
| `--safetynet` | Backup locals before pull overwrite |
| `--verify` | Sample-check after pull |
| `--verify-sample N` | Sample size (default 20) |
| `--quick` | Prefer files newer than last success |
| `--max-files` / `--max-bytes` | Batch limits |
| `--min-free BYTES` | Abort if free space too low |
| `--apfs-snapshot` | `tmutil localsnapshot` first |
| `--notify` | Notify on failure |
| `--no-speedtest` | Skip cable/iperf preflight |
| `--timeout SEC` | Budget per heavy step (default 3600) |
| `--no-progress` | Disable live progress bar |

### Presets

`documents`, `desktop`, `downloads`, `developer`, `pictures`, `movies`, `music`,
`library-app`, `ssh`, `config` — see `SYNC_PATH_PRESETS` in `constants.py`.

## Progress bar

On a TTY (not with `--json` / `--no-progress`), stderr shows a live line with
percent, direction/phase, size, rate, ETA, path.

## Default excludes

Trash, Caches, Xcode DerivedData, CoreSimulator, npm cacache, `node_modules`,
virtualenvs, `.DS_Store`, `.maccluster-safetynet/` —
see `SYNC_HOME_EXCLUDES` in `src/maccluster/constants.py`.

Optional file: `~/.config/maccluster/sync-excludes` (one pattern per line, `#` comments).

## Run history

JSON logs under `~/Library/Logs/maccluster/sync-YYYYMMDDTHHMMSSZ.json`  
Pointer: `~/Library/Logs/maccluster/sync-last.json`  
Quick-update watermark: `~/Library/Caches/maccluster/sync_state.json`

## Exit codes

| Code | Meaning |
|------|---------|
| 0 | All peers OK |
| 1 | All peers failed (often SSH) |
| 2 | Usage / no peer matched |
| 3 | Partial |

## What this is not

- Not full-disk clone / Carbon Copy Cloner / `asr`
- Not continuous FSEvents daemon (use `service sync-install` for interval)
- Not iCloud (System Settings → Apple ID if you want Apple’s cloud Home)
- Not automatic remote install of MacCluster — use `remote-install` / [PEER-SSH.md](./PEER-SSH.md)
