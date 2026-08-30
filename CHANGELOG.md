# Changelog

## Unreleased

## 0.4.0 — 2026-08-30

### Added — sync transport ladder `rdma` → `tb` → `wifi`

Inventory and planning are unchanged; the transfer stage of every sync
(`sync home`, `sync dev`, `pull`, `push`, `delta --apply`) now walks a per-peer
ladder and steps down one rung on failure:

| Prio | Rung | Data path | Available when |
|---|---|---|---|
| 1 | `rdma` | `arep xfer push\|pull` — autoreplikator moves the planned files over RDMA on the Thunderbolt link device (`services/sync_rdma.py`) | `arep status --json`: peer `trusted` + `rdma` in `transportCapable` |
| 2 | `tb` | existing ssh/scp/ditto, bound to the TB Self-IP | peer IP answers on `bridge0` |
| 3 | `wifi` | existing ssh/scp/ditto via `user@host.local`, no bind | `*.local` hostname in `cluster.toml` |

- A failing rung (exception or rc ≠ 0) logs exactly
  `transport downgrade <from>→<to>: <reason>`; after a partial run both sides
  are re-stat'ed for the planned files (`services/sync_replan.py`) so the next
  rung only carries what is still missing. Last rung failing keeps the old
  failure shape; no rung available → `no transport available: <reasons>`,
  rc −1. `--dry-run` never spawns arep.
- **`arep xfer` contract**: manifest as JSON-Lines `{"rel","size","mtimeNs"}` on
  stdin, progress as JSON-Lines `{"event":"progress","done","total"}` /
  `{"event":"done","bytes"}` / `{"event":"error","reason"}` on stdout, exit 0
  ok / non-zero abort. `arep` joins the subprocess allowlist (resolved from
  `~/.local/bin` too); an `error` event, non-zero exit, `--timeout` kill
  (rc 124) or a start failure all downgrade.
- **`cluster.toml`**: optional `transport_priority = ["rdma", "tb", "wifi"]`
  (default). Validated: non-empty, known names only, no duplicates; dumped by
  `config show` only when non-default.
- **CLI**: `--transport rdma|tb|wifi` on `sync home` / `sync dev` forces one
  rung — no probing of the others, no downgrade; an unavailable rung fails the
  peer with the probe reason. On `sync dev` it is exclusive with
  `--no-wifi` / `--wifi-only` and disables the Wi-Fi top-N pass.
- **Output**: peer rows show `transport=<rung>` and the downgrade lines;
  progress phase reads `transfer transport=rdma`; `--json` adds
  `peers[].transport`, `peers[].downgrades`, `transport_priority`.
- **`doctor`**: new finding `rdma_no_device_to_peer` (WARN, advisory — exit
  code unchanged) when `rdma_ctl` reports RDMA enabled but `arep status --json`
  lists no rdma-capable peer; `rdma_device_to_peer` INFO otherwise. arep is only
  queried when RDMA is enabled. Nothing switches `rdma_ctl` (Recovery-OS only).

Spec: autoreplikator `docs/superpowers/specs/2026-08-29-rdma-transport-design.md` §5.
Docs: `docs/SYNC-HOME.md` → "Transport ladder".

## 0.3.2 — 2026-08-29

### Fixed — topo matches peers via Thunderbolt domain UUID
- system_profiler names a TB peer only by model code ("Mac16,11"), and the
  parser even kept the local bus Device Name ("MacBook Pro") as peer — with
  two identical Mac minis the topology could never tell which cable goes to
  which node (`matched=-`, `unmatched peers`). The parser now reads the
  nested attached-device block (real peer model + the peer port's own
  Domain UUID → `ThunderboltPort.peer_domain_uuid`), and matching prefers
  that UUID against a new optional per-node `tb_domain_uuids` list in
  cluster.toml, falling back to hostname matching. `topo` now renders
  `peer=Mac16,11 matched=node-a` per link.

## 0.3.1 — 2026-08-28

### Added — fleet commands, host doctor snapshot, mesh bench, TB-gateway guard
- **`maccluster push` / `pull` / `delta`**: Home + `~/Developer` transfer shortcuts
  (`home_dev_transfer`), byte-accurate delta planning (`precise_delta`)
- **`doctor --host [--fleet]`**: optional RAM/load/disk/thermal host snapshot;
  `--fleet` hops each peer over the TB bridge; default `doctor` stays fast
- **`heal --fleet [--together] [--dry-run]`**: heal this Mac, then run
  `maccluster heal` on each peer over the TB bridge; missing remote CLI or
  required sudo is reported per-hop instead of failing the whole run
- **`bench --mesh`**: sequential directed full-mesh iperf3 across all nodes
- **`up` shows the TB link count**: `interface=bridge0 ip=… tb_links=2` — the
  bridge is one interface, but you can now see how many cables actually carry
  a live Mac↔Mac link (`MutateResult.tb_links`, also in `--json`)
- **Thunderbolt Bridge must not kill Wi-Fi**: a `Router` left on the TB Bridge
  service made macOS prefer it as the default gateway and dropped internet;
  `up` / `heal` now strip that Router and reorder services Wi-Fi-first,
  TB-last; `doctor` warns on `tb_gateway` if a router is still set
  (`maccluster.services.wifi_guard`)

### Fixed
- `heal --fleet --dry-run` no longer attempts a real `protect_wifi_from_bridge`
  apply call

## 0.3.0 — 2026-08-27

### Changed — push streams instead of staging an archive

`push` piped through four serial phases per batch: hardlink the files into a
stage dir, `ditto -c` that into `push.cpio`, `scp` the archive, then `ditto -x`
it on the peer. Three full passes over the same bytes, and the link sat idle
during two of them. Counted over a real run: the wire moved data in **27%** of
wall time (`extract` 6465 samples, `transfer` 4681, `archive` 4312, `stage`
2192).

The archive now goes straight into the peer: `ditto -c stage -` piped into
`ssh peer 'ditto -x - dest'`. Packing, transfer and unpacking overlap. Metadata
semantics are unchanged — verified that xattrs survive the stream roundtrip
identically to the file path. `--no-stream` restores the old behaviour.

Measured on this Thunderbolt pair to size the gap, same 416 MB / 22,820-file
corpus:

| transport | throughput |
|---|---|
| `maccluster sync` (staged) | ~10 MB/s |
| straight pipe, 1 stream | 36 MB/s |
| straight pipe, 2 streams | 47 MB/s |
| straight pipe, 4 or 8 streams | 47-48 MB/s |

Parallelism saturates at two streams, so the ceiling is APFS file creation
(2,653 files/s), not the network: the same link carries 45.8 Gbit/s with iperf3
and 110 MB/s for a single large file over `scp`. For a tree of small files no
transport can beat the filesystem — but it can stop idling, and that is the 4.8x
this change targets (34.66 GB projected 59 min staged vs ~12 min piped).

### Added
- `ProcessRunnerPort.run_pipe(producer, consumer)` — runs two argv concurrently.
  A non-zero producer status wins over a consumer that exited 0 after reading a
  truncated stream; producer stderr goes to a temp file because draining it from
  a pipe while waiting on the consumer can deadlock.
- `--no-stream` on `sync home` / `sync dev`

### Not verified
The streaming push has unit coverage and `run_pipe` was exercised against real
`ditto` processes, but no end-to-end run over SSH to a live peer: the cluster
peer went offline mid-sync (`Connection reset by peer`, then unreachable on
Thunderbolt, `.local` and Tailscale alike) and this Mac has Remote Login off.

## 0.2.9 — 2026-08-27

### Fixed — sync re-copied the whole tree instead of the delta

`sync dev` planned an 83.5 GB push against a peer it had barely looked at.
Measured after the fix, on the same pair: the real plan is 34.66 GB push
(398,825 files) plus 19.97 GB pull (62,203 files). So the truncation caused a
2.4x over-push and hid the pull direction almost entirely, because files only
the peer had were never listed. Three defects compounded:

- **The remote walk spawned one Python interpreter per directory.** That child
  process was added in 0.2.3 to survive iCloud/FileProvider hangs, but it costs
  a process start per folder: measured 167 files/s. `scandir` now runs
  in-process for `sync dev`, whose tree (`~/Developer`) never reaches a cloud
  provider. `sync home` can reach iCloud-backed Documents/Desktop and keeps the
  killable child, so the 0.2.3 fix stands where it was needed. Force either way
  with `MACCLUSTER_INV_SAFE_SCANDIR=1`. Measured on the cluster peer:
  **889,344 files in 38 s (23,404/s) instead of 40,050 in 240 s** — 140x faster,
  and the walk finishes instead of being cut off. In the real sync path the
  remote inventory went from 40,050 files (truncated) to 372,156 (complete),
  and the whole compare from 4:57 to 1:22.
- **A truncated inventory was returned as if it were complete.** At 167 files/s
  the 240 s budget always tripped, leaving 0.9% of a 4.4M-file tree listed. The
  code detected this (`# inventory time budget`), wrote it into a note string,
  and planned against the partial list anyway. Worse, the empty case returned
  `{}` "so push can proceed" — which reads as "the peer has nothing".
- **`plan_transfers` read absence as deletion-worthy fact.** A file missing from
  the inventory became `only_local` and was pushed, so every file the walk never
  reached was re-sent.

`_remote_inventory` now reports completeness, `plan_transfers` takes
`remote_complete`, and files the walk never reached are counted as
`remote_unknown` and left for the next run rather than blindly pushed. Default
budget raised 240s -> 900s and the SSH-side cap 360s -> 1200s so the cap can no
longer kill the walk before its own budget applies.

### Changed
- `MACCLUSTER_INV_MAX_SEC` default 240 -> 900
- New: `MACCLUSTER_INV_SAFE_SCANDIR=1` forces per-directory child processes;
  `sync home` sets it automatically, `sync dev` does not

## 0.2.8 — 2026-08-26

### Changed — dependency refresh
- `ruff` 0.13.3 → **0.16.4**, `pytest` 9.0.3 → 9.1.1, `rich` 14.2.0 → 15.0.0,
  `hatchling` 1.31.0 → 1.32.0
- CI actions: `actions/checkout` 4 → 7, `actions/setup-python` 5 → 7

These six Dependabot PRs had been sitting open since early August, all showing
red CI. The failures were stale: the runs dated from 2026-08-01 and had been
executed against a much older `main`. After rebasing each branch onto current
`main`, every check passed — the bumps themselves were never the problem.
Verified on the merge result: 220 tests pass, `ruff check` clean, `ruff format`
reports 231 files already formatted.

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
