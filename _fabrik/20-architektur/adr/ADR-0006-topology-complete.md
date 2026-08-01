# ADR-0006: Topology complete definition

| Field | Value |
|---|---|
| Number | ADR-0006 |
| Status | Accepted |
| Date | 2026-08-01 |
| Project | maccluster |

## Context

A-022 requires `topo` to show detected links (domain UUID when available, ports/receptacles, peer association) and match against config. A-023 forbids automatic physical cabling recommendations (“plug cable from X to Y”). Analysis OP-7 left the definition of `Topology.complete` open; domain model DM-5 suggested no full-mesh cabling mandate. Operators may wire line, star, or partial mesh among 2–4 nodes.

## Options considered

### Option 1: complete = full mesh (every pair has a TB link)

- **Pros:** Strict physical connectivity guarantee.
- **Cons:** Not required by product goals; many valid studio layouts are line/chain; false “incomplete” alarms.

### Option 2: complete = all config peers reachable by ping

- **Pros:** Simple operational definition.
- **Cons:** Ignores useful TB domain/link evidence when ping blocked; conflates L3 with L1/L2.

### Option 3: complete = each config peer is ping-reachable **or** domain/link-matched

- **Pros:** Matches analysis suggestion; works for partial mesh; still actionable.
- **Cons:** Slightly more logic; “matched but not pingable” needs clear UI.

### Option 4: complete only if operator-declared cable plan matches

- **Pros:** Explicit.
- **Cons:** Extra config surface not in requirements; out of scope for v1.

## Decision

**`Topology.complete == true` if and only if every config peer (non-self node) satisfies at least one of:**

1. **Reachability:** ICMP ping (or configured reachability probe) reports up, **or**
2. **Link match:** a local TB link/domain observation associates that peer (hostname/domain/UUID/hint match rules in `topology/match.py`) with confidence above the documented threshold.

Additional rules:

- Unmatched observed ports/links are listed as `unmatched` — never as cabling advice (A-023).
- Partial cluster (2 of 4 up) is valid degraded operation; `complete` may be false without crashing (A-030).
- No SPF/graph optimization or rewiring suggestions in v1.
- Output may show match confidence; low confidence still shows raw evidence.

## Consequences

**Positive:**

- Aligns topo success with operator reality (line/star/partial mesh)
- Closes OP-7 for implementation and tests
- Keeps scope free of cable-planner features

**Negative / risks:**

- Heuristic peer association can mis-label (R-F02) — mitigate with explicit unmatched section and config IP cross-check when available
- Definition must be documented in README so Exit/status wording stays consistent
