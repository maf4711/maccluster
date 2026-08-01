# ADR-0001: CLI framework — argparse vs Typer

| Field | Value |
|---|---|
| Number | ADR-0001 |
| Status | Accepted |
| Date | 2026-08-01 |
| Project | maccluster |

## Context

MacCluster exposes ~12 subcommands (`tb`, `init`, `config`, `up`, `heal`, `status`, `monitor`, `topo`, `doctor`, `bench`, `service`) plus global flags (`--config`, `--json`, `-v`). Brief G and NFA demand Python 3.11+, stdlib-first, minimal dependencies, and cold start under ~1.5 s for `--help` (NFA-007). Draft **schnell** proposed Typer for faster scaffolding; drafts **pragmatisch**, **agentenfreundlich**, and **skalierbar** preferred stdlib argparse. Leadership directed: no Typer when stdlib suffices.

## Options considered

### Option 1: stdlib argparse

- **Pros:** Zero runtime dependency; explicit parser tree in one file; easy for agents to extend without framework magic; smaller install and import surface.
- **Cons:** More boilerplate for subcommands and help text; no automatic type coercion beyond argparse features.

### Option 2: Typer (Click-based)

- **Pros:** Less boilerplate; nice help; fast to generate command stubs.
- **Cons:** Runtime dependency + transitive Click; more magic; conflicts with stdlib-first brief; larger CVE/SCA surface; slightly worse cold start.

### Option 3: Click directly

- **Pros:** Mature, widely known.
- **Cons:** Same dependency cost as Typer without Typer ergonomics; still not required.

## Decision

Use **stdlib `argparse`** exclusively for CLI parsing and dispatch.

- Entry: `maccluster.cli.main:main`
- Parser tree lives in `maccluster/cli/parser.py`
- Subcommand handlers in `maccluster/commands/*` are plain callables — no framework decorators
- Help and error messages in English

## Consequences

**Positive:**

- Runtime `install_requires` can stay empty (aside from optional `rich`)
- Aligns with hybrid architecture (pragmatisch stack + agent-friendly modules)
- Dispatch table is reviewable and merge-friendly for parallel agents

**Negative / risks:**

- Slightly more verbose command wiring — mitigated by thin command modules and a single parser file
- No Typer `CliRunner` — tests use `subprocess` / `python -m maccluster` or invoke `main(argv=...)` with patched context
