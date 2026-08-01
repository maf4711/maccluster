# ADR-0002: Config path and format

| Field | Value |
|---|---|
| Number | ADR-0002 |
| Status | Accepted |
| Date | 2026-08-01 |
| Project | maccluster |

## Context

Cluster desired state (name, subnet, bridge interface, 2–4 nodes with IPs and identities) must be portable across members, human-editable, and free of a database. Analysis AD-6 / A-040 fix the default path and override order. A-042 requires `schema_version`. Brief specifies TOML.

## Options considered

### Option 1: TOML at `~/.config/maccluster/cluster.toml`

- **Pros:** Matches AD-6; XDG-like; human-readable; `tomllib` in Python 3.11+; portable copy between nodes (A-008).
- **Cons:** `tomllib` is read-only — write path needs template/emitter.

### Option 2: JSON config

- **Pros:** stdlib read/write; easy schema tests.
- **Cons:** Less operator-friendly comments; Brief/analysis specify TOML.

### Option 3: YAML

- **Pros:** Familiar to some operators.
- **Cons:** Extra dependency (PyYAML) or fragile subset parser; not in Brief.

### Option 4: macOS defaults / plist as primary config

- **Pros:** Native feel.
- **Cons:** Harder to version in git/dotfiles; weaker cross-node portability story.

## Decision

1. **Format:** TOML with required `schema_version` (integer ≥ 1; v1 writes `1`).
2. **Default path:** `~/.config/maccluster/cluster.toml`.
3. **Resolution order:** CLI `--config PATH` > Env `MACCLUSTER_CONFIG` > Default.
4. **Read:** stdlib `tomllib`.
5. **Write (`init`):** deterministic template / hand-written emitter for schema v1; new files mode `0600`.
6. **Overwrite:** refuse without `--force`; with `--force`, backup to `.bak` or timestamp-`.bak` (A-004).
7. **Example:** ship `examples/cluster.toml` with placeholders (2–4 nodes, `10.42.0.0/24`).

Missing or invalid config on dependent commands → Exit **2**, message includes resolved expected path (A-027).


## Security addendum (2026-08-01)

From architecture security review (`SICHERHEIT.md` S-02):

1. **Symlink policy:** Before create/overwrite of the resolved config path, `lstat` the path. If it is a symbolic link, refuse with Exit **2** and do not write (RF-X-11 / RF-A21). Same rule for backup targets when using `--force`.
2. **Atomic replace:** Write to a temp file in the **same directory** as the destination, then `os.replace`.
3. **Mode:** New files `0600`.
4. **Size cap:** Reject configs larger than **1 MiB**.
5. **No secrets** in schema v1 fields or examples.

## Consequences

**Positive:**

- Single portable source of truth; operator can version via dotfiles
- No DB or cloud
- Clear test surface for path resolution and validation

**Negative / risks:**

- Custom TOML writer must be tested for round-trip of schema v1 fields
- If schema grows a lot later, consider a dedicated writer library via new ADR
