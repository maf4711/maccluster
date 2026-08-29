# `maccluster sync home` — CCC-inspired Home sync over Thunderbolt

MacCluster **bring-up** (`up` / `heal` / `status`) only manages the TB mesh IP stack.
It does **not** clone disks. Home file sync is an explicit CLI layer, inspired by
useful **Carbon Copy Cloner** ideas (compare, filters, SafetyNet, verify, schedule)
— but **two-way**, **Home-only**, **no deletes**, over a transport ladder
`rdma` (arep) → `tb` (SSH over the bridge) → `wifi` (SSH via `.local`), with
Apple `ditto` on the SSH rungs. See [Transport ladder](#transport-ladder-rdma--tb--wifi).

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
peer in `cluster.toml`. `sync home` has no top-N pass; it only reaches Wi-Fi
as the last rung of the transport ladder. `--transport` on `sync dev` replaces
both passes with a single one on the chosen rung.

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
| **`ssh` / `scp`** | Transport over TB fixed IPs (`10.42.0.x`) or `.local` (rungs `tb` / `wifi`) |
| **`arep`** (optional) | Rung `rdma`: RDMA over the Thunderbolt link device, fed by a manifest |
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
5. **Transport ladder** picks the first usable rung (`rdma` → `tb` → `wifi`,
   see below)  
6. **Push** — `rdma`: plan handed to `arep xfer push`; `tb` / `wifi`:
   hardlink-stage → `ditto -c` → `scp` → remote `ditto -x`  
7. **Pull** — same in reverse (`arep xfer pull` / `ditto`)  
8. Optional **verify** sample + **run log** JSON  

## Transport ladder (`rdma` → `tb` → `wifi`)

Inventory and planning never change. What changes per peer is **how the planned
bytes move**: the transfer stage walks a ladder of transports and steps down one
rung when the current one fails.

| Prio | Rung | Data path | Available when |
|---|---|---|---|
| 1 | `rdma` | `arep xfer push\|pull` — autoreplikator (`arep`) moves the files over **RDMA on the Thunderbolt link device** (bypasses `bridge0`, encrypted with the arep session key) | `arep status --json` lists the peer with `trust = "trusted"` **and** `"rdma"` in `transportCapable` (arep found a link device straight to that peer) |
| 2 | `tb` | `ssh` / `scp` / `ditto` to `user@10.42.0.x`, bound to the TB Self-IP | the peer's cluster IP answers `ping` on the bridge |
| 3 | `wifi` | same `ssh` / `scp` / `ditto` to `user@<host>.local`, never bound | a `*.local` hostname for the peer in `cluster.toml` (+ SSH user) |

The peer is looked up in `arep status --json` by `displayName` (Bonjour name,
`.local` and case ignored) or `fingerprint` against the node's `hostnames` and
`id`. `arep` is optional: without it (or with `arep status` failing) the `rdma`
rung is simply skipped and the reason shows up in the peer row. MacCluster
never enables RDMA itself — `rdma_ctl` is Recovery-OS only.

### Order, downgrade, re-plan

- Order = `transport_priority` from `cluster.toml` (default
  `["rdma", "tb", "wifi"]`), filtered to the rungs that are available right now.
- The first rung runs **push**, then **pull**. If it raises or returns rc ≠ 0
  the run logs **exactly one line** and continues with the next rung:

  ```text
  transport downgrade <from>→<to>: <reason>
  ```

  Examples: `transport downgrade rdma→tb: arep exit 3: link lost`,
  `transport downgrade tb→wifi: push rc=255: ssh: connect to host 10.42.0.2 port 22: No route to host`.
  `<reason>` is the arep `error` reason / exit code + stderr tail / timeout for
  `rdma`, and `push|pull rc=N: <first stderr line>` (≤ 160 chars) for the SSH
  rungs. The line goes to the progress notes, is printed under the peer row in
  plain output and is kept verbatim in `peers[].downgrades` (`--json`).
- After a **partial** rung (some bytes already moved) both sides are re-stat'ed
  for the planned files only and the plan is recomputed, so the next rung only
  carries what is still missing — nothing is transferred twice.
- Last rung fails → the usual failure shape (rc ≠ 0 + stderr, peer FAIL).
  No rung available → `no transport available: <reasons>`, rc −1 for that peer.
- `--dry-run` / `--compare` **never spawn arep**: the first rung is reported and
  the existing SSH dry-run summaries are produced.

### Config key — `transport_priority`

```toml
# ~/.config/maccluster/cluster.toml (top level, optional)
transport_priority = ["rdma", "tb", "wifi"]   # default when omitted
```

Rules (`config validate` exits **2** otherwise): array of strings, non-empty,
only `rdma` / `tb` / `wifi`, no duplicates. `config show` prints the key only
when it differs from the default. Typical variants:

```toml
transport_priority = ["tb", "wifi"]     # never try arep / RDMA
transport_priority = ["rdma", "tb"]     # never fall back to Wi-Fi
```

### CLI flag — `--transport rdma|tb|wifi`

`sync home` and `sync dev` accept `--transport` to **force exactly one rung**:
no probing of the others, no downgrade. An unavailable forced rung fails the peer
with the probe reason (e.g. `unavailable: arep peer trust=<state> (run arep pair)`
or `unavailable: 10.42.0.2 not reachable on bridge`);
an unknown name is a usage error (exit **2**).

```bash
maccluster sync home --transport rdma --peer node-b   # RDMA or fail, no fallback
maccluster sync home --transport tb                    # classic TB ssh/ditto only
maccluster sync dev  --transport wifi                  # whole ~/Developer over .local
```

On `sync dev`, `--transport` is mutually exclusive with `--no-wifi` /
`--wifi-only` and **disables the Wi-Fi top-N pass**: the tree runs once on the
chosen rung (`--transport wifi` moves the whole tree over `.local`, not just the
recent repos).

### What you see

- Progress bar phase: `transfer transport=rdma` (also `tb` / `wifi`)
- Notes: `transport=rdma → node-b`, then any `transport downgrade …` lines
- Plain peer row: `[OK] node-b (10.42.0.2) via a321@10.42.0.2 [tb] transport=rdma push=… pull=…`
- `--json`: `peers[].transport` (rung that ran last, `""` if none),
  `peers[].downgrades` (exact lines, in order), `transport_priority` (ladder
  order this run used)
- `maccluster doctor`: `rdma_no_device_to_peer` **WARN** when `rdma_ctl` reports
  RDMA enabled but `arep status --json` lists no peer with `rdma` in
  `transportCapable` (detail: `arep peers=N; check TB link + pairing`). It is
  advisory and does not change the doctor exit code. With a usable path the
  finding is `rdma_device_to_peer` INFO (`rdma path to 1 peer(s): mac-mini-b`);
  with RDMA off or `rdma_ctl` missing: `rdma peer path not assessed`. `doctor`
  only calls `arep` when RDMA is enabled.

### The `arep xfer` contract (rung 1)

MacCluster keeps inventory and planning; arep moves the bytes. Per direction it
spawns one process (allowlisted binary, resolved from `~/.local/bin`,
`/opt/homebrew/bin`, `/usr/local/bin`, `/usr/bin`; minimal env
`PATH`/`HOME`/`USER`/`LANG=C`):

```text
arep xfer push|pull --node <cluster.toml node id> --manifest -
```

**stdin — manifest, JSON-Lines, one file per line, plan order:**

```json
{"rel": "Documents/notes.txt", "size": 1234, "mtimeNs": 1756450000123456789}
```

`rel` is relative to the tree root (`~` for `sync home`, `~/Developer` for
`sync dev`), `size` in bytes, `mtimeNs` nanoseconds since the epoch — taken from
the local inventory for `push` and from the remote inventory for `pull`. Every
planned file must be present in the inventory; a missing entry aborts before
arep is spawned (a silently dropped file would look like a successful sync).

**stdout — progress, JSON-Lines (non-JSON lines are ignored):**

```json
{"event": "progress", "done": 1048576, "total": 5242880}
{"event": "done", "bytes": 5242880}
{"event": "error", "reason": "link lost"}
```

`done` / `total` are bytes and feed the progress bar; `done.bytes` (else the
last `progress.done`) is the byte count reported for the rung.

**exit code:** `0` = all files transferred, `1` (any non-zero) = abort. An
`error` event, a non-zero exit, a kill after `--timeout` (default 3600 s,
reported as rc 124) or a process that cannot start all raise a transport failure
→ downgrade to the next rung. Empty manifest = no-op, arep is not spawned.

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
5. Optional, for the `rdma` rung: `arep` in `~/.local/bin` on both Macs, peers
   paired (`arep pair`), `arep status --json` showing the peer as `trusted`
   with `rdma` in `transportCapable`. Without it the ladder starts at `tb`.

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
maccluster sync home --transport tb          # skip arep/RDMA, classic ssh/ditto
maccluster sync home --transport rdma --peer node-b
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
| `--timeout SEC` | Budget per heavy step (default 3600); also the `arep xfer` kill timeout |
| `--no-progress` | Disable live progress bar |
| `--transport rdma\|tb\|wifi` | Force one rung of the transport ladder (no downgrade); `sync dev`: single pass, no Wi-Fi top-N |
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
