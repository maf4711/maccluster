# FAQ — Developers / Integrators

| Field | Value |
|---|---|
| Product | MacCluster |
| Version | 0.1.0 |
| Date | 2026-08-01 |
| Audience | Contributors, automation authors, local integrators |

## What’s new in 0.1.0

- Python 3.11+ package (`src/maccluster`), entry point `maccluster`.
- Hexagonal layout: ports + adapters (OS tools mocked in tests).
- 102 pytest tests (unit + integration fixtures); no live 4-node mesh in CI.
- Optional JSON output (`--json`, field `schema_version`) for scripting.

## Build & test

```bash
python3 -m pip install -e ".[dev]"
make verify          # ruff + pytest
python3 -m pytest -q
ruff check src tests
```

On non-macOS CI hosts:

```bash
export MACCLUSTER_SKIP_PLATFORM_GUARD=1
```

Do **not** set that env in production.

## Architecture (short)

| Layer | Role |
|---|---|
| `cli/` | argparse, exit codes 0/1/2/3 |
| `commands/` | thin command handlers |
| `services/` | use-cases (status, mutate, doctor, …) |
| `domain/` | models, invariants |
| `ports/` | interfaces |
| `adapters/` | `system_profiler`, ifconfig, launchctl, ping, … |
| `config/` | TOML load/validate/paths |

Mutating path (`up` / `heal`) is **local Self only** via shared ensure plan + file lock. No remote write over SSH.

### Sync transport ladder (`services/`)

| Module | Role |
|---|---|
| `transport_ladder.py` | Pure decision logic: `probe_transports` (arep status / bridge ping / `.local` target), `choose_transports(probe, priority, override)`, `TransportFailed` |
| `sync_transport.py` | Transfer stage: `select_transports` + `run_transfer_ladder` walk `rdma` → `tb` → `wifi`, emit `transport downgrade <from>→<to>: <reason>` (`downgrade_line`), return `TransferOutcome` |
| `sync_rdma.py` | Rung `rdma`: manifest JSON-Lines → `arep xfer push\|pull --node <id> --manifest -` on stdin, progress JSON-Lines from stdout, exit ≠ 0 → `TransportFailed` |
| `sync_replan.py` | After a partial rung: re-stat both sides for the planned rels only, re-run `plan_transfers` |

`sync_service.sync_home` still does inventory + planning and only delegates the
transfer; it must not grow. Config: `ClusterConfig.transport_priority`
(`DEFAULT_TRANSPORT_PRIORITY = ("rdma", "tb", "wifi")` in `domain/models.py`),
parsed/validated in `config/load.py`. CLI: `--transport` in `cli/parser.py`.
Doctor: `checks.check_rdma_device_to_peer` → `rdma_no_device_to_peer` (WARN, not
a cluster-degrading id). Both subprocess paths (`arep status --json`, `arep xfer`)
are injectable, so `tests/unit/services/test_transport_ladder.py`,
`test_sync_transport.py` and `test_sync_rdma.py` run without arep. The
`arep xfer` contract is documented in
[SYNC-HOME.md](../SYNC-HOME.md#the-arep-xfer-contract-rung-1).

## CLI surface (automation)

| Command | Mutation | Typical exit |
|---|---|---|
| `tb` | no | 0 |
| `init` / `init --force` | config file | 0 / 2 if exists without force |
| `config show\|validate` | no | 0 / 2 |
| `up` / `heal` | local net | 0 / 1 / 2 / 3 |
| `heal --loop` | local net | long-running; Ctrl+C → 0 |
| `status` / `monitor` / `topo` / `doctor` | no | 0 / 3 degraded |
| `bench <target>` | no | 0 / 1 missing iperf3 / 2 usage |
| `service install\|uninstall\|status` | plist | 0 |

Global: `--config`, `--json`, `-v`.  
Env: `MACCLUSTER_CONFIG`, `NO_COLOR`, `MACCLUSTER_RICH=0`, `MACCLUSTER_SKIP_PLATFORM_GUARD` (tests only).

### JSON contract

```bash
maccluster --json status
# stdout: object with schema_version; errors still JSON + non-zero exit
```

Stable enough for scripts: nodes (`id`, `ip`, reachability), timestamps where applicable. Not a public HTTP API.

### Exit codes (AD-3)

| Code | Meaning |
|---|---|
| 0 | OK / healthy |
| 1 | Runtime / privileges / missing iperf3 |
| 2 | Usage, validation, unsupported platform for mutate |
| 3 | Degraded (peer down; up without TB link but IP set) |

## Extension points

- **Fake ports** in tests: inject adapters via app factory patterns used in `tests/`.
- **Config schema:** `schema_version = 1` required; bump only with explicit migration story.
- **Bridge name:** `bridge_interface` in TOML; mutate is fail-closed if mapping is ambiguous.
- **Optional rich:** monitor TUI if `rich` installed; plain path remains complete.

## Local zero-cost notes

- No cloud LLM/SaaS clients; import scan covered by tests.
- Subprocess only allowlisted absolute paths, `shell=False`, timeouts.
- Fixtures under `tests/fixtures/` (system_profiler sample, configs).

## Contributing

1. Keep platform scope: **macOS Apple Silicon Mac mini** for mutate paths.
2. Prefer pure domain + fixture tests over live hardware in CI.
3. No secrets in examples, logs, or JSON.
4. English for code, comments, README, product FAQs.

## Troubleshooting

| Symptom | Check |
|---|---|
| Platform guard exit 2 on Linux CI | `MACCLUSTER_SKIP_PLATFORM_GUARD=1` for tests only |
| Privilege tests flaky | Mutate must raise clear privilege error without root |
| Mapping tests fail | Fail-closed is intentional — see `tests/unit/mapping/` |
| Import of network clients | Offline tests must stay green |

## Related

- [README](../../README.md)
- [Operator FAQ](./operator.md)
- [Receptacle mapping](../receptacle-mapping.md)
- Example: `examples/cluster.toml`
