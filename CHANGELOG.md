# Changelog

## 0.1.8 — 2026-08-02

### Fixed
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
