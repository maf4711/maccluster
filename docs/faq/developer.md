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
