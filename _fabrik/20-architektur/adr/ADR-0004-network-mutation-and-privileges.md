# ADR-0004: Network mutation and privileges

| Field | Value |
|---|---|
| Number | ADR-0004 |
| Status | Accepted |
| Date | 2026-08-01 |
| Project | maccluster |

## Context

`up` and `heal` must configure Thunderbolt bridge and fixed Self-IP (A-009–A-013, A-038) **only on the local host** (A-041). Wrong interface mutation can break Wi-Fi/LAN (R-T04, R-D02). Admin/sudo is often required on macOS for interface changes (A-012, A-028, NFA-019). Concurrent mutators must not corrupt state (A-031). Security baseline forbids shell-string invocation (A-044).

## Options considered

### Option 1: Direct ifconfig/networksetup via argv ProcessRunner + allowlist

- **Pros:** Transparent; fixture-mockable; least privilege for read path; matches brief OS-tool list.
- **Cons:** CLI semantics vary; need careful idempotent ensure steps.

### Option 2: System Configuration framework via PyObjC

- **Pros:** More “native”.
- **Cons:** Heavy dependency; harder testing; not brief-default.

### Option 3: Root helper daemon / setuid helper

- **Pros:** LaunchAgent could mutate without interactive sudo.
- **Cons:** Large security surface; packaging complexity; deferred by AD-4 / OP-5 for v1.

### Option 4: Remote mutation over SSH to peers

- **Pros:** “Cluster-wide up from one node”.
- **Cons:** **Forbidden** by A-041; expands blast radius.

## Decision

1. **Local only:** `NetworkApplyPort` mutates only the executing host’s allowlisted TB/bridge interfaces. No SSH write, no remote ifconfig.
2. **Split ports:** `NetworkReadPort` (no root) vs `NetworkApplyPort` (may need admin).
3. **ProcessRunner only:** `subprocess` with `shell=False`, argv lists, timeouts; basename allowlist + **absolute** path resolution (see Security addendum); includes `ifconfig`, `networksetup`, etc.
4. **Allowlist targets:** interface names from config override or mapping result only; never default route, DNS globals, or Wi-Fi power as side effects.
5. **Ensure path (shared by up and heal):** plan pure `HealAction[]` → apply ordered steps (bridge present, interface up, Self-IP present); idempotent (A-010, NFA-016).
6. **Privileges:** preflight; if insufficient rights → Exit **1**, message contains `admin/sudo required`; no silent partial success as Exit 0. No interactive sudo from library code — operator elevates the shell.
7. **Lock:** host-local file lock `~/.config/maccluster/mutate.lock` (PID + stale takeover) around mutators and service install/uninstall.
8. **up without TB link:** still set bridge/IP if possible → Exit **3** with “no TB link” (AD-5 / A-011).
9. **dry_run:** apply functions accept `dry_run: bool` for tests.


## Security addendum (2026-08-01)

From architecture security review (`SICHERHEIT.md` S-01, S-03, S-04):

1. **ProcessRunner path safety:** Allowlisted basenames only (`ifconfig`, `networksetup`, …). Resolve to **absolute** paths under `/usr/sbin`, `/sbin`, `/usr/bin`, `/bin` (plus Homebrew paths only for optional `iperf3`). Never invoke via untrusted `PATH` when elevated.
2. **Child environment:** Pass a minimal env (`PATH=/usr/bin:/bin:/usr/sbin:/sbin`, `HOME`, `USER`, locale as needed). Do **not** forward `DYLD_*` or the full parent environment (RF-X-09).
3. **Interface identifiers:** Validate with `^[A-Za-z][A-Za-z0-9_.-]{0,15}$` before any apply (RF-F3-15).
4. **Forbidden apply ops:** no default-route changes, no global DNS, no Wi-Fi power, no non-allowlisted interfaces, no remote mutation (A-041).
5. **Lock file:** `mutate.lock` uses the same non-symlink write policy as config (see ADR-0002 addendum).

## Consequences

**Positive:**

- Clear security boundary; testable apply plan without real hardware
- Symmetry preserved (same binary; each node mutates self)
- Scriptable exit codes for automation

**Negative / risks:**

- Without root, heal/LaunchAgent may log failure after reboot until operator runs `sudo maccluster heal` (see ADR-0005)
- ifconfig/networksetup drift still possible — keep steps small and documented
