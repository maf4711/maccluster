# ADR-0003: Thunderbolt probing strategy

| Field | Value |
|---|---|
| Number | ADR-0003 |
| Status | Accepted |
| Date | 2026-08-01 |
| Project | maccluster |

## Context

F1 / A-001–A-002 require listing Thunderbolt/USB4 ports: capability, link speed or unconnected, peers/domain hints — **without admin**. A-039 requires receptacle→interface mapping that is fixture-testable and fail-closed on ambiguity for mutations. OS surfaces (`system_profiler`, `ioreg`) drift across macOS versions (risks R-F01, R-D01, R-T01). No stable public Apple API is mandated by the brief; product stack is Python + host tools.

## Options considered

### Option 1: system_profiler only (SPThunderboltDataType)

- **Pros:** Simple; one tool; readable JSON/XML text for fixtures.
- **Cons:** Single point of format drift; may miss details ioreg has.

### Option 2: ioreg only

- **Pros:** Rich IOKit tree.
- **Cons:** Harder to parse; more brittle text; less operator-familiar.

### Option 3: Dual chain — system_profiler primary, ioreg fallback

- **Pros:** Resilience to partial failures; better coverage; drafts agentenfreundlich/skalierbar recommend this.
- **Cons:** Two parsers to maintain; need merge rules.

### Option 4: PyObjC / IOKit bindings

- **Pros:** Structured APIs.
- **Cons:** Heavy dep; platform coupling; out of stdlib-first style; harder CI fixtures.

## Decision

**Dual probe chain with pure parsers:**

1. **Primary:** `system_profiler` (Thunderbolt / USB4 data) via ProcessRunner.
2. **Fallback / enrich:** `ioreg` when primary missing fields, empty, or parse-soft-fail.
3. **Parse layer:** pure functions (`adapters` fetch text → pure parse modules / functions) fed by **fixtures** in CI (NFA-048).
4. **Mapping:** isolated `mapping/receptacle.py` + `layouts.py` (known Mac mini tables); config `bridge_interface` / interface override wins when set.
5. **Ambiguity:** mutating ops (`up`/`heal`) **fail closed** Exit **2** with doctor/tb guidance (A-039); never silently pick Wi-Fi/`en0`.
6. **Rights:** probes run without root; no sudo prompt (A-002).

Merge rule: prefer primary for port identity and link speed; use fallback to fill interface hints and domain UUID when absent.

## Consequences

**Positive:**

- Read-only TB info without admin
- CI can test parsers without live hardware
- Fail-closed mapping protects host networking

**Negative / risks:**

- Two parsers need fixture samples from real minis (redacted)
- OS upgrades may break formats → doctor should surface parse source / warnings
- Mapping tables may need README updates per Mac mini generation
