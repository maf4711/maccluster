# `maccluster sync home` — CCC-inspired Home sync over Thunderbolt

MacCluster **bring-up** (`up` / `heal` / `status`) only manages the TB mesh IP stack.
It does **not** clone disks. Home file sync is an explicit CLI layer, inspired by
useful **Carbon Copy Cloner** ideas (compare, filters, SafetyNet, verify, schedule)
— but **two-way**, **Home-only**, **no deletes**, over TB/SSH with Apple `ditto`.

```bash
maccluster delta                            # inventory → precise deltas (report)
maccluster delta --apply                    # transfer only the difference
maccluster delta --peer node-b --limit 1
maccluster pull                             # two-way Home + ~/Developer
maccluster push                             # local → peer Home + ~/Developer
maccluster pull --dry-run --peer node-b
maccluster sync home --compare
maccluster sync home --dry-run
maccluster sync home --safetynet --verify
maccluster sync home --preset documents,developer --peer node-b
maccluster sync dev --compare
maccluster sync dev
```

## `sync dev` — Developer tree

`maccluster sync dev` (alias `sync developer`) is the same engine with a
**different tree root**: `$HOME/Developer` on both Macs, not `$HOME`.

| | `sync home --preset developer` | `sync dev` |
|---|---|---|
| Tree | `$HOME` | `$HOME/Developer` |
| Inventory | walks Home, keeps `Developer/` | walks only Developer |
| `.git` | remote inventory skips hidden dirs | included (`.git`, `.github`, …) |
| Logs | `<home>/Library/Logs/maccluster/` | always `~/Library/Logs/maccluster/` |

Use `sync dev` when the goal is “keep `~/Developer` aligned across the mesh”
(repos, `.env`, dirty work, git objects). `--include repo/` limits to one
project. `--home` / `--remote-home` override the Developer path.

### Wi-Fi top-N recent repos

`sync dev` always does the Thunderbolt full-tree pass first (same as 0.2.5).
It **also** copies the **10 most recently touched top-level git repos** over
Wi-Fi, so recent work still moves if the TB path is down or you are only on
WLAN.

### MCPRT before ditto

Every `sync dev` **starts with MCPRT** on those recent git repos:

1. Merge an open PR on the current branch (`gh pr merge --squash`), if any  
2. Commit remaining work (never `.env` / keys / `credentials.json`)  
3. `git fetch` + `merge --no-edit origin/<branch>` + push + tags  
4. **TestFlight** (`intern` + `Extern`) when the repo looks like an iOS app  

Then the TB/Wi-Fi ditto pass runs (so `.env` and other skip-paths still copy
over the mesh). `--dry-run` / `--compare` only *report* MCPRT. `--no-mcprt`
skips it; `--no-testflight` skips only the archive/upload.

| | Thunderbolt | Wi-Fi |
|---|---|---|
| Payload | whole `~/Developer` | top N git repos (default 10) |
| SSH | `user@10.42.0.x` + `BindAddress` Self-IP | `user@host.local` (no bind) |
| Ranking | — | `.git` / `HEAD` / `index` / `COMMIT_EDITMSG` mtime |

```bash
maccluster sync dev                 # TB + Wi-Fi top 10
maccluster sync dev --wifi-only     # WLAN recent repos only
maccluster sync dev --no-wifi       # TB only
maccluster sync dev --wifi-top 5
```

`--include` on the Wi-Fi pass is an **intersection** with the recent-repo
list. `--wifi-top 0` disables the pass. Needs a `*.local` hostname on the
peer in `cluster.toml`. `sync home` is unchanged (TB only).

## `maccluster delta` — inventory first, then difference

Unlike bulk size checks (`du`) or full-tree copies, **delta** always:

1. **Reads inventories** on this Mac and selected peers (all inventory peers,
   `--peer`, or first `--limit N`)
2. **Compares** path → `(mtime_ns, size)` with the conflict policy
3. **Reports** exact buckets with **file counts + byte totals**
4. With **`--apply`**, transfers **only** planned push/pull files via ditto

```bash
maccluster delta --no-speedtest
maccluster delta --peer node-b --preset ssh,config
maccluster delta --apply --safetynet --verify
```

## `maccluster pull` / `maccluster push` (shortcuts)

Daily one-liners for the paths that matter most across minis:

| Default presets | Paths under `$HOME` |
|-----------------|---------------------|
| documents, desktop, downloads | `Documents/`, `Desktop/`, `Downloads/` |
| **developer** | **`Developer/`** (`~/Developer`) |
| ssh, config | `.ssh/`, `.config/` |

```bash
maccluster pull                  # two-way, newer mtime wins
maccluster push                  # local → peer only (same scope)
maccluster push --both           # two-way like pull
maccluster pull --pull-only      # peer → local only
maccluster pull --push-only      # local → peer only
maccluster pull --full-home      # entire $HOME (still uses default excludes)
maccluster push --full-home
maccluster pull --preset developer   # override: only Developer/
```

Same engine and flags as `sync home` (SafetyNet, verify, peer, notify, …).

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
maccluster delta -v
maccluster delta --apply --peer node-b
maccluster delta --limit 2 --preset documents,developer
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
| `--no-wifi` | `sync dev` only: skip Wi-Fi recent-repo pass |
| `--wifi-only` | `sync dev` only: skip TB; Wi-Fi top-N only |
| `--wifi-top N` | `sync dev` only: how many recent git repos (default 10; 0 off) |
| `--no-mcprt` | `sync dev` only: skip merge/cpr/TestFlight preflight |
| `--no-testflight` | `sync dev` only: git ship, no TestFlight |

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
