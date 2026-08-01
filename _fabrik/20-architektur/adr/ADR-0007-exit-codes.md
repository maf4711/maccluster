# ADR-0007: Exit codes

| Field | Value |
|---|---|
| Number | ADR-0007 |
| Status | Accepted |
| Date | 2026-08-01 |
| Project | maccluster |

## Context

Operators and scripts need stable process exit codes. Analysis AD-3 / A-X1 / A-X2 / NFA-045 (as refined) require four codes: success, runtime error, usage/validation, and degraded partial success. Earlier NFA-A17 mentioned only 0/1/2; AD-3 supersedes that for MacCluster. Commands differ: `status` vs `monitor` vs `doctor` vs mutators.

## Options considered

### Option 1: Only 0 and 1

- **Pros:** Simple.
- **Cons:** Cannot distinguish bad args from peer-down; breaks scripting (A-019).

### Option 2: 0 / 1 / 2 (no degraded)

- **Pros:** Common Unix CLI pattern.
- **Cons:** Forces peer-down into error or success incorrectly; AD-3 rejects this.

### Option 3: 0 / 1 / 2 / 3 with documented semantics (AD-3)

- **Pros:** Scriptable; matches analysis and stories US-020; clear degraded path for up without link.
- **Cons:** Callers must learn code 3; must be in README.

### Option 4: Many fine-grained codes (10+)

- **Pros:** Very detailed automation.
- **Cons:** Overkill for v1; unstable contract risk.

## Decision

Adopt **exactly four** exit codes, centralized in `maccluster/cli/exit_codes.py`:

| Code | Name | Meaning |
|---|---|---|
| **0** | `OK` | Success / healthy; info commands with readable output; monitor clean Ctrl+C; doctor with only optional info warnings (e.g. missing iperf3) |
| **1** | `ERROR` | Runtime/system/privilege failure; OS command failed; unhandled-but-caught crash path; `bench` when iperf3 missing |
| **2** | `USAGE` | Bad CLI args; missing/invalid config; validation; unsupported platform for mutate; mapping ambiguity fail-closed |
| **3** | `DEGRADED` | Partial cluster: ≥1 peer unreachable while self ok (`status`); `up` set bridge/IP but no TB link; doctor worst severity is cluster/bridge/reachability **warn** without **error** |

### Command-specific rules

| Command | Notes |
|---|---|
| `status` | all peers up → 0; ≥1 peer down, self ok → **3**; config hard-fail → 2; probe crash → 1 |
| `monitor` | stays running when peers down; on clean Ctrl+C → **0** |
| `up` | ok + link → 0; bridge/IP ok, no TB link → **3**; no rights → 1; bad config/mapping → 2 |
| `heal` | healthy noop → 0; corrected → 0; privilege fail → 1 |
| `doctor` | worst `error` → **1**; warn-cluster without error → **3**; only optional info → **0** |
| `init` | exists without `--force` → **2** |
| `bench` | no iperf3 → **1**; missing target arg → **2** |

### Implementation rules

- No scattered magic numbers; import constants from `exit_codes.py`.
- User-facing errors: English message on stderr (or JSON error envelope with `--json`); no traceback by default; `-v` may show stack.
- `--json` failures still emit valid JSON object with error info and non-zero exit (A-033).

## Consequences

**Positive:**

- Automation can treat 3 as “alert but not crash”
- Aligns product with AD-3 and acceptance tests
- Single module is the contract for agents and docs

**Negative / risks:**

- Scripts that treat any non-zero as hard fail will alert on degraded — document prominently in README
- Doctor severity mapping must stay consistent with A-X2 tests
