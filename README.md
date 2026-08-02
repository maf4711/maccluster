# MacCluster

CLI tool for operating **2–4 Apple Silicon Mac minis** as a Thunderbolt-networked
cluster. Same package on every member — no leader, no cloud, no database.

**Platform:** macOS · Apple Silicon (arm64)  
**Runtime:** Python 3.11+ · stdlib only (optional `rich` for monitor TUI)  
**License:** MIT

## Install

### One-liner (GitHub raw)

```bash
curl -fsSL https://raw.githubusercontent.com/maf4711/maccluster/main/install.sh | bash
```

### Download ZIP

| What | URL |
|------|-----|
| **ZIP (main)** | https://github.com/maf4711/maccluster/archive/refs/heads/main.zip |
| **raw install.sh** | https://raw.githubusercontent.com/maf4711/maccluster/main/install.sh |
| **Repo** | https://github.com/maf4711/maccluster |
| **Tag release ZIP** | https://github.com/maf4711/maccluster/archive/refs/tags/v0.1.0.zip |

```bash
curl -fsSL https://github.com/maf4711/maccluster/archive/refs/heads/main.zip -o maccluster.zip
unzip maccluster.zip && cd maccluster-main && ./install.sh
```

### pipx / pip from Git

```bash
pipx install "git+https://github.com/maf4711/maccluster.git"
# or a tag:
pipx install "git+https://github.com/maf4711/maccluster.git@v0.1.0"
```

### Local checkout (dev)

```bash
git clone https://github.com/maf4711/maccluster.git
cd maccluster
pipx install .          # or: python3 -m pip install -e ".[dev]"
./install.sh
make verify             # ruff + pytest
maccluster --help
```

## Offline / zero cloud

MacCluster runs entirely on the local Mac. It does not call remote LLM or SaaS
APIs. Host tools used: `system_profiler`, `ioreg`, `ifconfig`, `networksetup`,
`ping`, `launchctl` (and optional `iperf3` / `ssh`).

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
| `bench` | no | Optional `iperf3` to a peer IP |
| `service install\|uninstall\|status` | plist | User LaunchAgent → `heal --loop` |

Global flags: `--config`, `--json`, `-v` / `--verbose`.  
Env: `NO_COLOR`, `MACCLUSTER_CONFIG`, `MACCLUSTER_SKIP_PLATFORM_GUARD=1` (tests only),
`MACCLUSTER_RICH=0`.

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
