# MacCluster

CLI tool for operating **2–4 Apple Silicon Mac minis** as a Thunderbolt-networked
cluster. Same package on every member — no leader, no cloud, no database.

**Platform:** macOS · Apple Silicon (arm64)  
**Runtime:** Python 3.11+ · stdlib only (optional `rich` for monitor TUI)  
**License:** MIT

## Install

**Short guide:** [`docs/INSTALL.md`](docs/INSTALL.md)

```bash
curl -fsSL https://raw.githubusercontent.com/maf4711/maccluster/main/install.sh | bash
export PATH="$HOME/.local/bin:$PATH"
maccluster --version
```

| Artifact | URL |
|----------|-----|
| **raw install.sh** | https://raw.githubusercontent.com/maf4711/maccluster/main/install.sh |
| **ZIP (main)** | https://github.com/maf4711/maccluster/archive/refs/heads/main.zip |
| **Repo** | https://github.com/maf4711/maccluster |

```bash
# pipx from Git
pipx install "git+https://github.com/maf4711/maccluster.git"

# dev checkout
git clone https://github.com/maf4711/maccluster.git && cd maccluster
pipx install .   # or: python3 -m pip install -e ".[dev]"
make verify
```

## Offline / zero cloud

MacCluster runs entirely on the local Mac. It does not call remote LLM or SaaS
APIs. Host tools used: `system_profiler`, `ioreg`, `ifconfig`, `networksetup`,
`ping`, `launchctl` (and optional `iperf3` / `ssh` / `scp` / `ditto` for sync home).

## Configuration

Default path: `~/.config/maccluster/cluster.toml`

Override order:

1. `--config PATH`
2. Environment `MACCLUSTER_CONFIG`
3. Default path above

Example (4 nodes, subnet `10.42.0.0/24`): see [`examples/cluster.toml`](examples/cluster.toml).

```bash
maccluster init                  # write template (fills local hostname / HW UUID)
maccluster config show
maccluster config validate
```

Schema field `schema_version = 1` is required. Config mode is `0600`. Symlink
targets are refused for write/lock paths.

## Commands

| Command | Mutation | Notes |
|---|---|---|
| `tb` | no | Thunderbolt ports, capability, speeds, peers |
| `init` | config file | Template with subnet `10.42.0.0/24`; `--force` backups existing |
| `config show` | no | Print resolved config |
| `config validate` | no | Validate + self-match |
| `up` | yes (local) | Ensure bridge + fixed Self-IP; often needs admin |
| `heal` | yes (local) | One-shot ensure (same path as `up`) |
| `heal --loop` | yes | Periodic heal (default 30 s, min 5 s); **best-effort**, not HA |
| `status` | no | Nodes + reachability + TB link + **live TX/RX rates** (netstat deltas) |
| `monitor` | no | Live refresh (`--interval`) with TX/RX Mb/s, pps, errors; Ctrl+C → exit 0 |
| `topo` | no | Cable / topology map (no rewiring advice) |
| `doctor` | no | Diagnostics (config, self, TB, bridge, peers) |
| `bench` | no | Optional `iperf3` to a peer IP (bound to TB Self-IP) |
| `speedtest` | no | TB **cable grade** (40G ideal) + iperf3 over bridge; also runs at start of `sync home` / `remote-install` |
| `service install\|uninstall\|status` | plist | User LaunchAgent → `heal --loop` |
| `service sync-install\|sync-uninstall\|sync-status` | plist | Scheduled `sync home` (CCC schedule analogue) |
| `sync home` | files via SSH | Two-way **Home** via **Apple ditto** + CCC-inspired options (compare, presets, SafetyNet, verify, policies). See [`docs/SYNC-HOME.md`](docs/SYNC-HOME.md) |
| `remote-install` | peer install | Install wheel+config on peer over **TB bridge only** (`10.42.0.x`, BindAddress Self-IP). See [`docs/REMOTE-INSTALL.md`](docs/REMOTE-INSTALL.md) |
| `ssh-config` | OpenSSH | Write `~/.ssh/config.d/maccluster` so `10.42.0.*` uses bridge BindAddress |
| `keychain show\|push\|pull\|delete\|push-peer` | Keychain | Local store + TB `push-peer`. See [`docs/KEYCHAIN.md`](docs/KEYCHAIN.md) |

Global flags: `--config`, `--json`, `-v` / `--verbose`.  
Env: `NO_COLOR`, `MACCLUSTER_CONFIG`, `MACCLUSTER_SKIP_PLATFORM_GUARD=1` (tests only),
`MACCLUSTER_RICH=0`.

### Home sync (`maccluster sync home`)

Mesh bring-up does **not** copy files. To keep `~/` aligned across minis over TB,
MacCluster uses **Apple `ditto`** (metadata-complete: xattrs, ACLs, resource forks)
with **newest-wins** by mtime — not Homebrew rsync, not iCloud:

```bash
# needs: ssh key login to peers (stock macOS ditto + scp)
maccluster sync home --compare              # CCC-style diff only
maccluster sync home --dry-run              # preview transfers
maccluster sync home --safetynet --verify   # SafetyNet + sample verify
maccluster sync home --preset documents,developer
maccluster sync home --conflict-policy prefer-local
maccluster service sync-install --interval 3600   # hourly schedule
maccluster sync home --last                 # last run log
```

Requires working SSH (`ssh-copy-id user@10.42.0.x`). Details: [`docs/SYNC-HOME.md`](docs/SYNC-HOME.md),
SSH troubles: [`docs/PEER-SSH.md`](docs/PEER-SSH.md).

### Live traffic (status / monitor)

`status` and `monitor` sample macOS `netstat` counters for `bridge0` and Thunderbolt
member ports (`en2`/`en3`/`en4`). Rates need **two samples** (Δt ≥ ~0.4 s):

- First run after idle: shows cumulative bytes/packets, `rate=n/a`
- Second `status` within 2 minutes, or every `monitor` tick: **RX/TX b/s–Gb/s**, packets/s, error counters (+delta)

```text
traffic Δ1.5s:
  bridge0     RX   12.40 Mb/s (  1.2k pps)  TX    3.10 Mb/s (  400 pps)  err in/out 0/0 (+0/+0)
```

This is **interface throughput**, not application transaction rate. For saturation
tests use `maccluster bench` (iperf3).

### Exit codes

| Code | Meaning |
|---|---|
| **0** | OK / healthy |
| **1** | Error (runtime, privileges, missing iperf3 for bench) |
| **2** | Usage / validation / unsupported platform for mutate |
| **3** | Degraded (peer down; `up` without TB link but IP set) |

### Privileges

- **Read-only** (no root): `tb`, `status`, `monitor`, `topo`, reading `doctor`, `config`
- **Often admin:** `up`, `heal` (when correcting bridge/IP) — message contains `admin/sudo required`
- **User LaunchAgent:** `service install` writes `~/Library/LaunchAgents/com.maccluster.heal.plist`

## Typical workflow

```bash
# On each Mac mini (same logical config):
maccluster init
# Edit ~/.config/maccluster/cluster.toml — hostnames, hw_uuid, IPs 10.42.0.1–.4
maccluster config validate
sudo maccluster up          # set bridge0 + Self IP
maccluster status
maccluster monitor
maccluster service install  # optional background heal --loop
```

## Receptacle mapping

See [`docs/receptacle-mapping.md`](docs/receptacle-mapping.md). Mutate is
**fail-closed** if the Thunderbolt bridge interface cannot be resolved uniquely.

## Development

```bash
python3 -m pip install -e ".[dev]"
make verify
```

Tests use fixtures and fake adapters; no live 4-node cluster is required in CI.
On non-macOS CI hosts set `MACCLUSTER_SKIP_PLATFORM_GUARD=1`.

## FAQ

- [Operator](docs/faq/operator.md) — install, privileges, heal limits, troubleshooting  
- [Developer](docs/faq/developer.md) — build, tests, exit codes, automation

## Security notes

- Subprocess only via allowlisted absolute paths (`shell=False`, timeouts).
- Network mutation is **local Self-host only**; no default-route or Wi-Fi changes.
- No secrets in config, logs, or JSON output.

## Operations (this Mac)

See [`docs/ops.md`](docs/ops.md) for the live `studio-cluster` inventory and
member install scripts under [`scripts/`](scripts/).

```bash
./scripts/install-member.sh
sudo maccluster up          # once, admin password
maccluster service install  # LaunchAgent heal --loop
maccluster status
```
