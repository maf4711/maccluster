# ADR-0005: LaunchAgent user domain

| Field | Value |
|---|---|
| Number | ADR-0005 |
| Status | Accepted |
| Date | 2026-08-01 |
| Project | maccluster |

## Context

Soll-scope A-015–A-017 and NFA-012/013 require a background heal loop that restarts on crash and best-effort restores bridge/IP after reboot (A-038). Analysis AD-4 chooses User-Domain LaunchAgent (`gui/$(id -u)`, plist under `~/Library/LaunchAgents`) over system-wide root agents. OP-5 left root-helper detail to architecture.

## Options considered

### Option 1: User-Domain LaunchAgent running `maccluster heal --loop`

- **Pros:** No root install for plist; least privilege; matches AD-4; simple uninstall; same binary as interactive CLI.
- **Cons:** May lack rights to mutate network after reboot; runs in user GUI session domain (after login).

### Option 2: System LaunchDaemon (root)

- **Pros:** Can always mutate interfaces.
- **Cons:** Requires root to install; larger blast radius; conflicts with least-privilege story; harder symmetric operator UX.

### Option 3: Privileged helper tool (SMJobBless-style) + user agent

- **Pros:** Best of both long-term.
- **Cons:** Significant macOS packaging/security work; out of v1 simplicity mandate.

### Option 4: cron / no background service

- **Pros:** Trivial.
- **Cons:** Misses KeepAlive and documented service UX; weak A-015.

## Decision

**v1: User-Domain LaunchAgent only.**

| Item | Value |
|---|---|
| Label | `com.maccluster.heal` |
| Plist path | `~/Library/LaunchAgents/com.maccluster.heal.plist` |
| Domain | `gui/$(id -u)` via `launchctl bootstrap` / `bootout` |
| ProgramArguments | absolute path to `maccluster`, `heal`, `--loop`, `--config`, resolved config path |
| KeepAlive | true (or equivalent) so process restart ≤ 60 s (NFA-013) |
| Interval | heal loop default 30 s (config `heal_interval_seconds`, min 5 s) |
| ThrottleInterval | ≥ 10 s to avoid crash loops |

**Behavior:**

- `service install` — idempotent Exit 0; write plist + bootstrap
- `service uninstall` — idempotent Exit 0 if absent
- `service status` — installed yes/no, running yes/no if determinable, label/path, interval; no root required for user agent

**Privilege honesty:** If heal cannot mutate without admin, agent logs clear error and exits non-zero for that tick; **no silent success**. README documents: interactive `sudo maccluster up` / `sudo maccluster heal` after login when OS requires elevation. Root helper = future ADR only if Gate 4 rejects best-effort A-038 under this model.

## Consequences

**Positive:**

- Simple install/uninstall without system privileges for the agent itself
- Same code path as CLI heal (no second binary)
- Aligns with AD-4 and least privilege for read-only ops

**Negative / risks:**

- Post-reboot recovery depends on user session + sufficient rights (R1 in ARCHITEKTUR.md)
- GUI domain may not run when no user logged in — acceptable for studio Mac mini always-on user sessions; document limitation
