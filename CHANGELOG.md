# Changelog

## 0.2.7 — 2026-08-25

### Added — MCPRT before `sync dev`
- **`maccluster sync dev` always runs MCPRT first** (merge open PR + commit/push
  + TestFlight for iOS apps) on the same recent git repos, then the ditto
  TB/Wi-Fi pass
- Secrets (`.env`, `*.pem`/`*.p8`/`*.key`, `credentials.json`, …) are never
  committed; ditto still copies them over the mesh
- `--no-mcprt` skips the preflight (ditto only)
- `--no-testflight` ships git only (no archive/upload)
- Dry-run / `--compare` does not commit, push, or upload
- MCPRT failure does not abort ditto; overall exit 3 if git/TF failed and
  ditto succeeded

```bash
maccluster sync dev                 # MCPRT → TB + Wi-Fi top 10
maccluster sync dev --wifi-only     # MCPRT → Wi-Fi recent repos
maccluster sync dev --no-mcprt      # file copy only
maccluster sync dev --no-testflight # git ship, skip TestFlight
```

## 0.2.6 — 2026-08-25

### Added — `sync dev` Wi-Fi top-10 recent repos
- After the Thunderbolt full-tree pass, **`maccluster sync dev` also syncs
  the 10 most recently touched top-level git repos over Wi-Fi**
  (SSH to the peer's `*.local` hostname, no TB `BindAddress`)
- Ranking is cheap git metadata (`.git` / `HEAD` / `index` / `COMMIT_EDITMSG`),
  not a full tree walk. Gitfiles (worktrees) count
- `--no-wifi` — TB only (previous behaviour)
- `--wifi-only` — skip TB; only the recent-repo Wi-Fi pass
- `--wifi-top N` — how many repos (default 10; `0` disables)
- User `--include` intersects the Wi-Fi set
- Output tags each peer row `[tb]` / `[wifi]` and lists `wifi_repos=`

```bash
maccluster sync dev                 # TB full Developer + Wi-Fi top 10
maccluster sync dev --wifi-only     # WLAN only, recent repos
maccluster sync dev --no-wifi       # Thunderbolt only
maccluster sync dev --wifi-top 5
```

## 0.2.5 — 2026-08-25

### Added — `maccluster sync dev`
- **`maccluster sync dev`** (alias **`sync developer`**): two-way **`~/Developer`**
  tree sync over TB/SSH using the same Apple `ditto` engine as `sync home`
- Tree root is **Developer**, not Home — inventory walks only that directory
  (faster than `sync home --preset developer`)
- Remote inventory includes **`.git` / `.github`** (dot-dirs); Home sync still
  skips hidden dirs
- Run logs stay in **`~/Library/Logs/maccluster/`** (not `~/Developer/Library/…`)
- Extra excludes: `.build`, `.next`, `.turbo`, pytest/ruff caches, DerivedData
- Same flags as `sync home` (`--compare`, `--dry-run`, `--peer`, `--include`, …)
- Override tree with `--home PATH` / `--remote-home PATH`

```bash
maccluster sync dev --compare
maccluster sync dev --dry-run --peer node-b
maccluster sync dev
maccluster sync dev --include maccluster/
```

## 0.2.4 — 2026-08-11

### Added — fabric mesh health, RDMA probe, exo correlator, heal keepalive
- **Mesh health** on `status` / `doctor`: verdict `ok|partial|isolated|single`
  with peer up/down counts — **alive ≠ fully meshed** (bridge/TB self-alive
  vs all peers reachable)
- **RDMA read-only** in `tb`, `status`, `doctor` via `rdma_ctl status` (never
  enables; Recovery-OS only for `rdma_ctl enable`)
- **Optional exo correlation**: `status --exo` / `doctor --exo` probes local
  `http://127.0.0.1:52415/state` (topology nodes, lastSeen stale, runners,
  RDMA nodes, instances). Distinguishes http-alive vs exo mesh incomplete
- **Heal heartbeat + watchdog**: `heal --loop` writes
  `~/Library/Caches/maccluster/heal_heartbeat.json`; `service install` also
  installs `com.maccluster.heal-watchdog` (`heal --watchdog`) to kickstart
  a hung heal agent; doctor checks heartbeat freshness
- **Bench path quality**: iperf3 parses retransmits; reports
  `quality=excellent|good|marginal|poor` and flags (low throughput /
  retransmits) for TB fabric grading

## 0.2.3 — 2026-08-09

### Fixed — reliable Desktop/Documents sync over TB
- **Remote inventory no longer hard-fails** on iCloud/FileProvider hangs: killable
  per-directory scandir (subprocess timeout), unbuffered stdout (`PYTHONUNBUFFERED`
  + periodic flush), soft-empty inventory so push can still proceed
- **Stage skips dataless/unreadable** files; empty stage is soft-ok (nothing to pull)
- **Auto-batch ditto CPIO** transfers (~2 GiB / 120 files) — multi-10 GiB archives
  often failed with `ditto: cpio read error`
- **Direct `scp` for files ≥3 GiB** (e.g. 14 GiB Desktop screen recording, multi-GB
  PDFs, huge backup zips) instead of packing them into CPIO
- **Timeout stdout decode** uses `errors=replace` so partial inventory survives
  non-UTF8 noise

## 0.2.2 — 2026-08-09

### Added — iCloud force-materialize + 1:1 sync
- **`maccluster sync home --force-icloud`**: materialize iCloud `UF_DATALESS`
  stubs on local + peer (`brctl download` + timed open) before inventory
- **`maccluster sync home --identical`**: best-effort 1:1 — force-icloud +
  bidirectional transfer + verify; remaining cloud-only stubs skipped
- **`--icloud-timeout` / `--icloud-max-seconds`**: tune materialize budget
- Inventory (local + remote) **skips dataless stubs** so ditto/rsync no longer
  hang on un-materialized placeholders

## 0.2.1 — 2026-08-02

### Fixed — Keychain honesty + CLI bugs
- **`--account` works after subcommands**: `keychain show --account X` and
  `keychain --account X show` both parse (shared parent args)
- **No fake iCloud share**: help/messages/docs state login Keychain is **local only**
  (`security` cannot create synchronizable items)
- **`init` no longer errors** when disk + Keychain both exist: keeps disk unless
  `--force` (returns source `disk` | `keychain` | `template`)
- **`FakeKeychainStore`** accepts `raw=` / `label=` for API parity
- **Sync run logs follow the synced home**, not the caller's real home — test
  runs no longer write into `~/Library/Logs/maccluster/`
- **Keychain writes never use `-U`**: updating an existing item rewrites its
  ACL, blocks on a Keychain UI prompt (rc 124) and loses the item; delete +
  fresh add instead. Write failures now name their cause (locked login keychain
  over SSH vs. pending prompt)
- **`speedtest` reverse fallback**: when the peer's application firewall blocks
  inbound iperf3 data connections, run the peer as client toward a local server

### Added
- **`maccluster keychain push-peer <peer>`** — plant `cluster.toml` on peer over
  TB bridge SSH; attempt remote `keychain push` (file still planted if peer
  Keychain is locked over SSH)

## 0.2.0 — 2026-08-02

### Added — CCC-inspired `sync home` features
- **`--compare`** — Diff report only (only_local / only_remote / newer / equal)
- **`--preset` / `--include`** — Path presets (documents, desktop, developer, …)
- **`--exclude-from`** — Pattern file (`~/.config/maccluster/sync-excludes` default)
- **`--conflict-policy`** — `newer` | `larger` | `prefer-local` | `prefer-remote` | `skip-conflict`
- **`--safetynet`** — Backup overwritten local files to `~/.maccluster-safetynet/<ts>/`
- **`--verify` / `--verify-sample`** — Post-pull size/mtime sample check
- **`--quick`** — Prefer files touched since last successful run
- **`--max-files` / `--max-bytes`** — Batch limits / resume next run
- **`--min-free`** — Free-space preflight (local + peer)
- **`--apfs-snapshot`** — Opt-in `tmutil localsnapshot` before transfer
- **`--notify`** — Notification Center on failure
- **`--last`** — Show last run log (`~/Library/Logs/maccluster/sync-*.json`)
- **`service sync-install|sync-uninstall|sync-status`** — Scheduled sync LaunchAgent
- Helpers: `sync_filters`, `sync_history`, `sync_safetynet`, `sync_verify`

## 0.1.9 — 2026-08-02

### Fixed
- CI: `CableGrade` uses `StrEnum` (ruff UP042)
- Dev dependency: `pytest>=9.0.3` (CVE-2025-71176 / GHSA-6w46-j5rx-g56g tmpdir)
- Shared `node_ssh_user()` for remote-install + speedtest peer iperf3 SSH
- CI Ubuntu: `test_ditto_allowlisted` no longer requires host `/usr/bin/ditto`

## 0.1.8 — 2026-08-02

### Fixed
- Tool resolution searches `~/.local/bin` (pipx / user-local installs)
  for iperf3, ssh, scp — doctor now finds user-installed iperf3
- `remote-install` honors per-node `ssh_target` user from cluster.toml
  (fixes wrong `User $USER` in generated `~/.ssh/config.d/maccluster`)
- `scripts/remote-install.sh`: repair truncated `"${EXTRA[@]}"` expansion
  (bash syntax error) and make empty-args safe under `set -u`

### Added
- **TB cable grading** (40 Gb/s excellent, 20 Gb/s ok) in `domain/cable.py`
- `maccluster speedtest` — cable report + iperf3 over **bridge BindAddress**
- Startup speedtest on `remote-install` and `sync home`
- Doctor check `cable`; `tb` / `status` show cable verdict
- iperf3 `-B <self-ip>` so bench stays on TB bridge

## 0.1.7 — 2026-08-02

### Added
- **Bridge-only remote ops**: SSH/SCP bind to Self TB IP (`BindAddress` / `-b`)
- `maccluster remote-install <peer>` — install wheel + config over **10.42.0.x only**
- `maccluster ssh-config` — write `~/.ssh/config.d/maccluster` (Host 10.42.0.*)
- Refuses Wi‑Fi/LAN peer IPs for remote-install
- `scripts/build-peer-bootstrap.sh` — AirDrop package plants SSH pubkey for later remote-install
- Docs: [`docs/REMOTE-INSTALL.md`](docs/REMOTE-INSTALL.md)

## 0.1.6 — 2026-08-02

### Added
- Live **progress bar** on `maccluster sync home` (stderr): percent, phase, path, speed, ETA
  - Stages: ssh → inventory → plan sample → stage → archive → transfer → extract
  - Transfer uses chunked SSH `cat` streams for real byte progress + rate
  - `--no-progress` or `--json` disables the bar

## 0.1.5 — 2026-08-02

### Changed
- **`maccluster sync home`** now uses **Apple `ditto`** (not rsync) for metadata-complete
  copies (xattrs, ACLs, resource forks, quarantine)
  - Newest-wins via mtime inventory; stage → `ditto -c` CPIO → `scp` → `ditto -x`
  - Allowlist: `ditto`, `scp` (system `/usr/bin`); rsync removed from sync path
  - Docs: [`docs/SYNC-HOME.md`](docs/SYNC-HOME.md)

## 0.1.4 — 2026-08-02

### Added
- **`maccluster sync home`** — two-way Home directory sync over TB/SSH
  - Strategy: **newest-wins**, **no deletes**
  - Flags: `--dry-run`, `--peer`, `--push-only` / `--pull-only`, `--user`, `--exclude`, `--timeout`
  - Docs: [`docs/SYNC-HOME.md`](docs/SYNC-HOME.md)

## 0.1.3 — 2026-08-02

### Fixed
- Shared peer reachability helper (`health/reach.py`) for status, doctor, topo
- Peer TB link state inferred when Mac-to-Mac TB link + peer UP (shows 40G etc.)
- Topo no longer uses bare ping (missed TB-bridge routing)
- PATH conflict: document pipx primary; skill report hardened

## 0.1.2 — 2026-08-02

### Fixed / complete
- Peer reachability: ICMP ping with **`-S <self-ip>`** (TB bridge), fallback **TCP:22** when ICMP filtered
- Status shows probe method (`via=tcp:22` / `ping`)
- Example config restored to 4-node template; live 2-node under `examples/studio-live.toml`
- `scripts/remote-install.sh` for peer install over SSH
- Version sync `__version__` / pyproject **0.1.2**

## 0.1.1 — 2026-08-02

### Added
- Live TX/RX rates, pps, errors on `status`/`monitor` (netstat counter deltas)
- `traffic[]` in JSON output; cache under `~/Library/Caches/maccluster/`

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] — 2026-08-01

### Added

- Initial release of MacCluster CLI for Apple Silicon Mac mini Thunderbolt clusters.
- Commands: `tb`, `init`, `config show|validate`, `up`, `heal` [`--loop`], `status`,
  `monitor`, `topo`, `doctor`, `bench`, `service install|uninstall|status`.
- TOML cluster config (`schema_version = 1`) with default subnet `10.42.0.0/24`.
- Shared ensure path for `up` / `heal` (bridge + fixed Self-IP, local only).
- User-domain LaunchAgent for background heal loop.
- Optional `iperf3` bandwidth bench and optional SSH peer probes.
- Plaintext symbols + optional rich monitor; `--json` with `schema_version`.
- Exit codes: 0 ok, 1 error, 2 usage, 3 degraded.
