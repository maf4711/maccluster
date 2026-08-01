# FAQ — Operator

| Field | Value |
|---|---|
| Product | MacCluster |
| Version | 0.1.0 |
| Date | 2026-08-01 |
| Audience | Operators of 2–4 Apple Silicon Mac mini Thunderbolt clusters |

## What’s new in 0.1.0

- First release: CLI `maccluster` for Thunderbolt bridge cluster bring-up and health.
- Commands: `tb`, `init`, `config`, `up`, `heal`, `status`, `monitor`, `topo`, `doctor`, `bench`, `service`.
- TOML config (`schema_version = 1`), default subnet `10.42.0.0/24`.
- User LaunchAgent for background `heal --loop` (best-effort, not HA).
- Exit codes: 0 ok · 1 error · 2 usage · 3 degraded.

## Getting started

1. Install on each mini: `pipx install .` (or `./install.sh`).
2. `maccluster init` → edit `~/.config/maccluster/cluster.toml` (hostnames, `hw_uuid`, IPs).
3. Copy the same logical config to every member; run `maccluster config validate` on each.
4. On each host: `sudo maccluster up`.
5. Check: `maccluster status` · watch: `maccluster monitor` · optional: `maccluster service install`.

## FAQ

### How many nodes are supported?

Exactly **2–4** in v1. One node or five+ is rejected (exit **2**).

### Where is the config file?

Default: `~/.config/maccluster/cluster.toml`.  
Override: `--config PATH` (wins) or env `MACCLUSTER_CONFIG`.

### Do I need a leader node?

No. Same package and config structure on every member. Self is resolved per host (hostname / HW UUID).

### Which commands need admin?

- **No root:** `tb`, `status`, `monitor`, `topo`, reading `doctor`, `config show|validate`.
- **Often admin:** `up`, `heal` when changing bridge/IP — message includes `admin/sudo required`.
- **User LaunchAgent:** `service install` writes `~/Library/LaunchAgents/com.maccluster.heal.plist` (no system daemon).

### What does exit code 3 mean?

**Degraded**, not a crash: e.g. a peer is down, or `up` set bridge/IP but there is **no TB link**. Scriptable and distinct from exit 1 (error) and 2 (usage/config).

### Will the cluster heal after reboot automatically?

**Best-effort only.** `service install` runs `heal --loop` as a **user** LaunchAgent. If macOS requires root to create the bridge or set the IP, the agent cannot elevate silently — run `sudo maccluster heal` after login, or accept that recovery needs privileges. There is **no HA/SLA** promise.

### Can I run without internet?

Yes. Core commands use only local OS tools (`system_profiler`, `ifconfig`, `ping`, `launchctl`, …). No cloud login.

### Optional bandwidth test?

Install `iperf3`, start a server on the peer, then `maccluster bench <peer-ip>`. Missing `iperf3` fails only `bench` (exit 1), not the rest of the CLI.

### SSH probes?

Optional and **off by default** (`ssh_probes_enabled = false`). Status/monitor work with local ping only.

## Troubleshooting

| Symptom | Check |
|---|---|
| `config validate` exit 2 | Hostname/`hw_uuid` must match **this** Mac exactly once |
| `up` exit 1 | Run with `sudo`; message should mention admin/sudo |
| `up` exit 2 (mapping) | Ambiguous bridge — set `bridge_interface` explicitly; see [receptacle-mapping.md](../receptacle-mapping.md) |
| `up` exit 3 | Bridge/IP may be OK; cable/link missing — check `maccluster tb` |
| Peers always DOWN | `up` on **each** node; same subnet; TB cable; firewall allowing ping |
| LaunchAgent not recovering IP | Expected without root — run `sudo maccluster heal` |

## Where to get help

- README (install, commands, exit codes)
- [receptacle-mapping.md](../receptacle-mapping.md)
- Example config: `examples/cluster.toml`
- Developer notes: [developer.md](./developer.md)
