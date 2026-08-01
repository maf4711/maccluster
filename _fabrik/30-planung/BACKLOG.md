# BACKLOG — MacCluster

| Feld | Wert |
|---|---|
| Projekt | MacCluster (`maccluster`) |
| Phase | 3 PLANUNG |
| Stand | 2026-08-01 |
| Grundlage | `10-analyse/ANFORDERUNGEN.md`, `USER-STORIES.md`, `20-architektur/ARCHITEKTUR.md`, `STACK.md` |
| Status | **Verbindlich für die Implementierung.** Bei Widerspruch zur Architektur gilt die Architektur; bei Widerspruch zu `ANFORDERUNGEN.md` gilt `ANFORDERUNGEN.md`. |
| Begleitend | `wellen.json` (maschinenlesbar), `WELLEN.md` (Begründung) |

## Lesehinweis

- Jede Zeile unter **Dateibesitz (exklusiv)** ist ein Pfad relativ zur Projektwurzel
  `projects/maccluster/`. Nur die genannte Story legt diese Datei an oder ändert sie.
- Ein `/`-Suffix bezeichnet das gesamte Verzeichnis einschließlich aller darin angelegten Dateien.
- **Wellenregel:** Abhängigkeiten zeigen ausschließlich auf Stories **früherer** Wellen.
  Innerhalb einer Welle ist der Dateibesitz paarweise disjunkt.
- **Intra-Wellen-Reihenfolge** (verbindlich, wenn Stories derselben Welle aufeinander aufbauen):
  - W2: US-003 → US-002 → US-010 → US-026
  - W3: US-012 → US-001
  - W4: US-004 → US-005 → US-011
  - W5: US-006 → US-008 → US-017 → US-007 → US-020
  - W6: US-016 → US-013 → US-014 → US-015; parallel: US-009, US-018→US-019, US-021, US-022, US-023, US-024, US-025
- **Arbeitspaket-IDs:** US-001 … US-026 aus der ANALYSE. **US-000** = Gerüst (Welle 1).
- **CLI-Registry:** `src/maccluster/cli/parser.py` gehört **US-000** (vollständige Subcommand-Deklaration in W1).
  Fach-Stories ersetzen Handler-Module (`commands/*`, `services/*`), ohne `parser.py` zu ändern.
- **Additivrechte (eng begrenzt):**
  1. Adapter-Stories: in `app_factory.py` (US-000) nur eigene Port-Verdrahtung.
  2. **US-016:** in `commands/heal.py` nur `--loop`-Zweig.
  3. **US-017:** JSON-Zweig in `tb`/`status`/`topo`/`doctor`-Commands.
  4. **US-021:** optionaler SSH-Zweig in status/doctor-Services.
  5. **US-012 / US-024 / US-025:** README-Abschnitte RO / Install-Platform / Offline.
  6. **US-023:** Rich-Zweig in monitor command/service.
  7. **US-022:** Audit-Hook-Aufruf in `mutate_service`.
- Code/CLI/README: Englisch. Fabrik-Artefakte: Deutsch.
- Package-Root: `projects/maccluster/` (`src/maccluster/`).

## Abdeckungsmatrix A-IDs → Stories → Wellen

| A-ID | Prio | Stories | Wellen |
|---|---|---|---|
| A-001 | Muss | US-001 | 3 |
| A-002 | Muss | US-001, US-012 | 3 |
| A-003 | Muss | US-002 | 2 |
| A-004 | Muss | US-002, US-010 | 2 |
| A-005 | Muss | US-003 | 2 |
| A-006 | Muss | US-003 | 2 |
| A-007 | Muss | US-003 | 2 |
| A-008 | Soll | US-026, US-002 | 2 |
| A-009 | Muss | US-004 | 4 |
| A-010 | Muss | US-004 | 4 |
| A-011 | Muss | US-004, US-020 | 4, 5 |
| A-012 | Muss | US-004, US-011 | 4 |
| A-013 | Muss | US-005 | 4 |
| A-014 | Soll | US-016 | 6 |
| A-015 | Soll | US-013 | 6 |
| A-016 | Soll | US-014 | 6 |
| A-017 | Soll | US-015 | 6 |
| A-018 | Muss | US-006 | 5 |
| A-019 | Muss | US-006, US-020 | 5 |
| A-020 | Muss | US-007 | 5 |
| A-021 | Muss | US-007, US-012, US-023 | 3, 5, 6 |
| A-022 | Muss | US-008 | 5 |
| A-023 | Muss | US-008 | 5 |
| A-024 | Muss | US-009 | 6 |
| A-025 | Soll | US-018 | 6 |
| A-026 | Soll | US-019, US-009 | 6 |
| A-027 | Muss | US-010, US-003 | 2 |
| A-028 | Muss | US-011 | 4 |
| A-029 | Muss | US-012 | 3 |
| A-030 | Muss | US-020 | 5 |
| A-031 | Soll | US-004 | 4 |
| A-032 | Soll | US-021 | 6 |
| A-033 | Soll | US-017 | 5 |
| A-034 | Muss | US-000, US-024 | 1, 6 |
| A-035 | Muss | US-000, US-025 | 1, 6 |
| A-036 | Kann | US-022 | 6 |
| A-037 | Kann | US-023 | 6 |
| A-038 | Muss | US-005, US-013, US-016 | 4, 6 |
| A-039 | Muss | US-001, US-004, US-008 | 3, 4, 5 |
| A-040 | Muss | US-002, US-003, US-010, US-026 | 2 |
| A-041 | Muss | US-004, US-005 | 4 |
| A-042 | Muss | US-002, US-003 | 2 |
| A-043 | Muss | US-000, US-024 | 1, 6 |
| A-044 | Muss | US-000, US-011, US-021 | 1, 4, 6 |
| A-045 | Soll | US-000, US-006, US-009, US-018, US-021 | 1, 5, 6 |

**Prüfung:** A-001–A-045 vollständig zugeordnet (Muss 31 · Soll 12 · Kann 2).

---

# WELLE 1 — Projektgerüst

---

# US-000 — Projektgerüst (Build, Package, CI, CLI-Skeleton)

| Feld | Wert |
|---|---|
| ID | US-000 |
| MoSCoW | Muss |
| Abgedeckte Anforderungen | A-034, A-035, A-043, A-044, A-045 (Gerüst), G1–G5 |
| Abhängigkeiten (Story-IDs) | — |
| Welle | 1 |

## Story

Als **Entwickler** möchte ich **ein installierbares Python-Package-Gerüst mit argparse-CLI, Exit-Codes, Ports, ProcessRunner, verify-Kette und CI**, damit **Folge-Wellen nur Fachmodule in ein grünes Skelett legen**.

## Akzeptanzkriterien

### AK-1 — Package + Entry-Point
- `pip install -e .` → `maccluster --help` und `python -m maccluster --help` Exit 0; Subcommands A-X3 gelistet

### AK-2 — verify + CI
- `make verify` = ruff check + ruff format --check + pytest grün; CI-Workflow vorhanden

### AK-3 — Repo-Hygiene G1–G5
- LICENSE (MIT), Lockfile, CI, `tests/`, verify in README, CHANGELOG, .gitignore, Dependabot

### AK-4 — ProcessRunner Security
- kein `shell=True`; Allowlist; Timeouts; Tests mit Sonderzeichen und Non-Allowlist (A-044, A-045)

### AK-5 — Platform-Guard-API
- `assert_supported_for_mutate()` mockbar (A-043 Vorbereitung)

## Dateibesitz (exklusiv)

- `LICENSE`
- `README.md`
- `CHANGELOG.md`
- `pyproject.toml`
- `requirements-dev.txt`
- `uv.lock`
- `Makefile`
- `install.sh`
- `.gitignore`
- `.github/workflows/ci.yml`
- `.github/dependabot.yml`
- `src/maccluster/__init__.py`
- `src/maccluster/__main__.py`
- `src/maccluster/app_factory.py`
- `src/maccluster/constants.py`
- `src/maccluster/errors.py`
- `src/maccluster/cli/main.py`
- `src/maccluster/cli/parser.py`
- `src/maccluster/cli/exit_codes.py`
- `src/maccluster/domain/models.py`
- `src/maccluster/domain/enums.py`
- `src/maccluster/domain/invariants.py`
- `src/maccluster/ports/clock.py`
- `src/maccluster/ports/filesystem.py`
- `src/maccluster/ports/process.py`
- `src/maccluster/ports/thunderbolt.py`
- `src/maccluster/ports/network.py`
- `src/maccluster/ports/reachability.py`
- `src/maccluster/ports/service.py`
- `src/maccluster/ports/bench.py`
- `src/maccluster/ports/lock.py`
- `src/maccluster/ports/identity.py`
- `src/maccluster/ports/platform.py`
- `src/maccluster/ports/audit.py`
- `src/maccluster/adapters/process.py`
- `src/maccluster/adapters/clock.py`
- `src/maccluster/platform/guard.py`
- `src/maccluster/commands/__init__.py`
- `src/maccluster/commands/_stub.py`
- `src/maccluster/services/__init__.py`
- `tests/conftest.py`
- `tests/unit/cli/test_help.py`
- `tests/unit/cli/test_exit_codes.py`
- `tests/unit/adapters/test_process_runner.py`
- `tests/unit/platform/test_guard_stub.py`
- `tests/integration/test_cli_help.py`

## Hinweise zur Umsetzung

- Hatchling, Python ≥ 3.11, entry `maccluster = maccluster.cli.main:main`, runtime-deps leer, optional `[monitor]` rich.
- Domain: Dataclasses vollständig (Felder); Validierung in W2.
- Dispatch: Subcommands → Import `commands.<mod>` oder Fallback `_stub` Exit 2 „not implemented“.

---

# WELLE 2 — Config, Init, Self-Match, Config-Fehler

---

# US-003 — Config laden, anzeigen und validieren

| Feld | Wert |
|---|---|
| ID | US-003 |
| MoSCoW | Muss |
| Abgedeckte Anforderungen | A-005, A-006, A-007, A-027, A-040, A-042 |
| Abhängigkeiten (Story-IDs) | US-000 |
| Welle | 2 |

## Story

Als **Operator** möchte ich **die Cluster-Config anzeigen und validieren**, damit **ungültige IPs, doppelte Identitäten oder fehlende Pflichtfelder früh scheitern**.

## Akzeptanzkriterien

### AK-1 — `config show`: Name, Subnetz, Interface, Nodes, role self/peer
### AK-2 — Validierung: doppelte IP, Subnetz, schema_version → Exit 2, Feld benannt
### AK-3 — 1 oder >4 Nodes → Exit 2
### AK-4 — Self-Match genau 1; sonst Exit 2

## Dateibesitz (exklusiv)

- `src/maccluster/config/paths.py`
- `src/maccluster/config/schema.py`
- `src/maccluster/config/load.py`
- `src/maccluster/config/validate.py`
- `src/maccluster/platform/identity.py`
- `src/maccluster/adapters/identity_macos.py`
- `src/maccluster/adapters/platform_macos.py`
- `src/maccluster/services/config_service.py`
- `src/maccluster/commands/config_cmd.py`
- `tests/fixtures/configs/`
- `tests/unit/config/test_paths.py`
- `tests/unit/config/test_load.py`
- `tests/unit/config/test_validate.py`
- `tests/unit/platform/test_identity.py`
- `tests/unit/platform/test_guard_macos.py`
- `tests/unit/services/test_config_service.py`

---

# US-002 — Cluster initialisieren (`init`)

| Feld | Wert |
|---|---|
| ID | US-002 |
| MoSCoW | Muss |
| Abgedeckte Anforderungen | A-003, A-004, A-040, A-042 |
| Abhängigkeiten (Story-IDs) | US-000 |
| Welle | 2 |

## Story

Als **Operator** möchte ich **mit `init` eine TOML-Vorlage anlegen** (schema_version, Subnetz 10.42.0.0/24, Node-Stubs, Self vorausgefüllt).

## Akzeptanzkriterien

### AK-1 — Vorlage gültig, ≥2 Nodes, Self soweit möglich
### AK-2 — ohne `--force` kein Overwrite (Exit 2); mit `--force` Backup `.bak`
### AK-3 — Mode 0600, atomic write, kein Symlink-Ziel

## Dateibesitz (exklusiv)

- `src/maccluster/config/dump.py`
- `src/maccluster/config/init_template.py`
- `src/maccluster/adapters/filesystem.py`
- `src/maccluster/services/init_service.py`
- `src/maccluster/commands/init_cmd.py`
- `tests/unit/config/test_dump.py`
- `tests/unit/config/test_init_template.py`
- `tests/unit/adapters/test_filesystem.py`
- `tests/unit/services/test_init_service.py`
- `tests/integration/test_init_roundtrip.py`

---

# US-010 — Fehlerfall: fehlende oder unlesbare Config

| Feld | Wert |
|---|---|
| ID | US-010 |
| MoSCoW | Muss |
| Abgedeckte Anforderungen | A-027, A-004, A-040 |
| Abhängigkeiten (Story-IDs) | US-000 |
| Welle | 2 |

## Story

Als **Operator** möchte ich **bei fehlender/kaputter Config klare Exit-Codes** (2 usage / 1 permission) und Hinweis auf `init`.

## Dateibesitz (exklusiv)

- `tests/unit/config/test_config_errors.py`
- `tests/integration/test_config_missing.py`

## Hinweise

- Nur Tests; Produktionscode US-003. Intra-Welle nach US-003.

---

# US-026 — Config als portable TOML teilen

| Feld | Wert |
|---|---|
| ID | US-026 |
| MoSCoW | Soll |
| Abgedeckte Anforderungen | A-008, A-040 |
| Abhängigkeiten (Story-IDs) | US-000 |
| Welle | 2 |

## Story

Als **Operator** möchte ich **dieselbe TOML auf allen Members** und ein **4-Node-Beispiel** im Repo.

## Dateibesitz (exklusiv)

- `examples/cluster.toml`
- `tests/unit/config/test_example_cluster_toml.py`
- `tests/integration/test_config_portable_self.py`

---

# WELLE 3 — Thunderbolt, Mapping, Plain-Render

---

# US-001 — Thunderbolt-Hardware-Info und Receptacle-Mapping

| Feld | Wert |
|---|---|
| ID | US-001 |
| MoSCoW | Muss |
| Abgedeckte Anforderungen | A-001, A-002, A-039 |
| Abhängigkeiten (Story-IDs) | US-000 |
| Welle | 3 |

## Story

Als **Operator** möchte ich **`maccluster tb`** und **fixture-testbares Receptacle→Interface-Mapping** (fail-closed bei Ambiguität).

## Dateibesitz (exklusiv)

- `src/maccluster/mapping/receptacle.py`
- `src/maccluster/mapping/layouts.py`
- `src/maccluster/adapters/tb_system_profiler.py`
- `src/maccluster/adapters/tb_ioreg.py`
- `src/maccluster/services/tb_service.py`
- `src/maccluster/commands/tb.py`
- `docs/receptacle-mapping.md`
- `tests/fixtures/system_profiler/`
- `tests/fixtures/ioreg/`
- `tests/unit/mapping/`
- `tests/unit/adapters/test_tb_system_profiler.py`
- `tests/unit/adapters/test_tb_ioreg.py`
- `tests/unit/services/test_tb_service.py`
- `tests/unit/commands/test_tb.py`

---

# US-012 — Read-only ohne Root + Plaintext-Symbole

| Feld | Wert |
|---|---|
| ID | US-012 |
| MoSCoW | Muss |
| Abgedeckte Anforderungen | A-029, A-002, A-021 |
| Abhängigkeiten (Story-IDs) | US-000 |
| Welle | 3 |

## Story

Als **Operator** möchte ich **RO-Befehle ohne Root** und **Zustände ohne reine Farbe** (Symbols, sanitize, NO_COLOR).

## Dateibesitz (exklusiv)

- `src/maccluster/render/symbols.py`
- `src/maccluster/render/plain.py`
- `src/maccluster/render/sanitize.py`
- `tests/unit/render/test_symbols.py`
- `tests/unit/render/test_plain.py`
- `tests/unit/render/test_sanitize.py`
- `tests/unit/render/test_no_color.py`

---

# WELLE 4 — Bring-up, Heal einmalig, Privilegien

---

# US-004 — Cluster Bring-up (`up`)

| Feld | Wert |
|---|---|
| ID | US-004 |
| MoSCoW | Muss |
| Abgedeckte Anforderungen | A-009, A-010, A-011, A-012, A-031, A-039, A-041 |
| Abhängigkeiten (Story-IDs) | US-000, US-001, US-002, US-003 |
| Welle | 4 |

## Story

Als **Operator** möchte ich **`up`**: Bridge + Self-IP, idempotent, Lock, lokal only, Exit 3 ohne TB-Link.

## Dateibesitz (exklusiv)

- `src/maccluster/adapters/network_read.py`
- `src/maccluster/adapters/network_apply.py`
- `src/maccluster/adapters/lock_file.py`
- `src/maccluster/heal_logic/plan.py`
- `src/maccluster/heal_logic/idempotency.py`
- `src/maccluster/services/mutate_service.py`
- `src/maccluster/commands/up.py`
- `tests/fixtures/ifconfig/`
- `tests/unit/heal_logic/`
- `tests/unit/adapters/test_network_read.py`
- `tests/unit/adapters/test_network_apply.py`
- `tests/unit/adapters/test_lock_file.py`
- `tests/unit/services/test_mutate_service.py`
- `tests/unit/commands/test_up.py`
- `tests/integration/test_mutate_guard.py`

---

# US-005 — Heal einmalig

| Feld | Wert |
|---|---|
| ID | US-005 |
| MoSCoW | Muss |
| Abgedeckte Anforderungen | A-013, A-038, A-041 |
| Abhängigkeiten (Story-IDs) | US-000, US-002, US-003 |
| Welle | 4 |

## Story

Als **Operator** möchte ich **einmaliges `heal`** (Drift korrigieren, Post-Reboot best-effort, nur lokal).

## Dateibesitz (exklusiv)

- `src/maccluster/commands/heal.py`
- `tests/unit/commands/test_heal.py`
- `tests/unit/services/test_heal_oneshot.py`
- `tests/unit/services/test_heal_post_reboot.py`

## Hinweise

- Ruft `mutate_service.ensure()` (US-004). **Intra-Welle nach US-004** starten (nicht parallel).
- `--loop` → US-016 (Additivrecht).

---

# US-011 — Admin-/sudo-Bedarf klar melden

| Feld | Wert |
|---|---|
| ID | US-011 |
| MoSCoW | Muss |
| Abgedeckte Anforderungen | A-028, A-012, A-044 |
| Abhängigkeiten (Story-IDs) | US-000 |
| Welle | 4 |

## Story

Als **Operator** möchte ich **klare Privilege-Meldungen** bei Mutationen und **keine sudo-Prompts** bei RO.

## Dateibesitz (exklusiv)

- `tests/unit/services/test_privilege_messages.py`
- `tests/unit/adapters/test_process_special_chars.py`
- `tests/integration/test_readonly_no_sudo.py`

## Hinweise

- Nur Tests; Intra-Welle nach US-004/US-005.

---

# WELLE 5 — Status, Monitor, Topo, JSON, Partial

---

# US-006 — Status-Snapshot

| Feld | Wert |
|---|---|
| ID | US-006 |
| MoSCoW | Muss |
| Abgedeckte Anforderungen | A-018, A-019, A-045 |
| Abhängigkeiten (Story-IDs) | US-000, US-003 |
| Welle | 5 |

## Story

Als **Operator** möchte ich **`status`**: Nodes, IP, Reachability, Timestamp; Exit 3 bei Peer-down; Ping-Timeout ≤ 2 s.

## Dateibesitz (exklusiv)

- `src/maccluster/adapters/ping_macos.py`
- `src/maccluster/health/snapshot.py`
- `src/maccluster/health/aggregate.py`
- `src/maccluster/services/status_service.py`
- `src/maccluster/commands/status.py`
- `tests/unit/health/test_snapshot.py`
- `tests/unit/health/test_aggregate.py`
- `tests/unit/adapters/test_ping_macos.py`
- `tests/unit/services/test_status_service.py`
- `tests/unit/commands/test_status.py`
- `tests/integration/test_status_exit_codes.py`

---

# US-007 — Live-CLI-Monitor

| Feld | Wert |
|---|---|
| ID | US-007 |
| MoSCoW | Muss |
| Abgedeckte Anforderungen | A-020, A-021 |
| Abhängigkeiten (Story-IDs) | US-000, US-003 |
| Welle | 5 |

## Story

Als **Operator** möchte ich **`monitor`** mit 1–2 s Refresh, TB-Link-Hinweis, Ctrl+C Exit 0, Partial-Cluster robust.

## Dateibesitz (exklusiv)

- `src/maccluster/services/monitor_service.py`
- `src/maccluster/commands/monitor.py`
- `tests/unit/services/test_monitor_service.py`
- `tests/unit/commands/test_monitor.py`

## Hinweise

- Nutzt HealthSnapshot (US-006) per Typvertrag. **Intra-Welle nach US-006**.

---

# US-008 — Topologie-Map

| Feld | Wert |
|---|---|
| ID | US-008 |
| MoSCoW | Muss |
| Abgedeckte Anforderungen | A-022, A-023, A-039 |
| Abhängigkeiten (Story-IDs) | US-000, US-001, US-003 |
| Welle | 5 |

## Story

Als **Operator** möchte ich **`topo`** mit Domain-UUID/Links, Config-Match, ohne Kabelführungs-Empfehlung.

## Dateibesitz (exklusiv)

- `src/maccluster/topology/match.py`
- `src/maccluster/topology/build.py`
- `src/maccluster/services/topo_service.py`
- `src/maccluster/commands/topo.py`
- `tests/fixtures/topology/`
- `tests/unit/topology/`
- `tests/unit/services/test_topo_service.py`
- `tests/unit/commands/test_topo.py`

## Hinweise

- Inkl. Fixture `partial_mesh` für US-020-Tests (Verzeichnisbesitz US-008).
- `Topology.complete` = Ping ∨ Domain/Link-Match (ADR-0006).

---

# US-017 — Optionales JSON-Output

| Feld | Wert |
|---|---|
| ID | US-017 |
| MoSCoW | Soll |
| Abgedeckte Anforderungen | A-033 |
| Abhängigkeiten (Story-IDs) | US-000, US-001 |
| Welle | 5 |

## Story

Als **Operator** möchte ich **`--json`** mit `schema_version` für status/tb/topo/doctor.

## Dateibesitz (exklusiv)

- `src/maccluster/render/json_out.py`
- `tests/unit/render/test_json_out.py`
- `tests/integration/test_status_json_schema.py`
- `tests/integration/test_json_tb_topo.py`

## Hinweise

- Additivrecht JSON-Zweige in Commands; Doctor-JSON nach US-009.

---

# US-020 — Peer down und Partial-Cluster

| Feld | Wert |
|---|---|
| ID | US-020 |
| MoSCoW | Muss |
| Abgedeckte Anforderungen | A-019, A-030, A-011 |
| Abhängigkeiten (Story-IDs) | US-000 |
| Welle | 5 |

## Story

Als **Operator** möchte ich **bei partial mesh stabile up/down-Ausgaben** ohne Tool-Crash; status Exit 3.

## Dateibesitz (exklusiv)

- `tests/integration/test_partial_cluster.py`
- `tests/unit/health/test_partial_mesh.py`

## Hinweise

- Fixtures unter `tests/fixtures/topology/` gehören US-008.
- **Intra-Welle zuletzt** (nach status/monitor/topo).

---

# WELLE 6 — Doctor, Service, Loop, Bench, SSH, Kann, Doku

---

# US-009 — Doctor / Diagnose (Basis)

| Feld | Wert |
|---|---|
| ID | US-009 |
| MoSCoW | Muss |
| Abgedeckte Anforderungen | A-024, A-026, A-045 |
| Abhängigkeiten (Story-IDs) | US-000, US-001, US-003, US-006 |
| Welle | 6 |

## Story

Als **Operator** möchte ich **`doctor`** mit ok/warn/fail und Exit-Semantik A-X2.

## Dateibesitz (exklusiv)

- `src/maccluster/doctor_logic/checks.py`
- `src/maccluster/doctor_logic/report.py`
- `src/maccluster/services/doctor_service.py`
- `src/maccluster/commands/doctor.py`
- `tests/unit/doctor_logic/`
- `tests/unit/services/test_doctor_service.py`
- `tests/unit/commands/test_doctor.py`
- `tests/integration/test_doctor_exit.py`

---

# US-016 — Heal im Loop

| Feld | Wert |
|---|---|
| ID | US-016 |
| MoSCoW | Soll |
| Abgedeckte Anforderungen | A-014, A-038 |
| Abhängigkeiten (Story-IDs) | US-000, US-005 |
| Welle | 6 |

## Story

Als **Operator** möchte ich **`heal --loop`** (Default 30 s, Min 5 s), best-effort, Ctrl+C sauber.

## Dateibesitz (exklusiv)

- `src/maccluster/services/heal_loop_service.py`
- `tests/unit/services/test_heal_loop_service.py`

## Hinweise

- Additivrecht `--loop` in `commands/heal.py`. Vor US-013.

---

# US-013 — LaunchAgent Service install

| Feld | Wert |
|---|---|
| ID | US-013 |
| MoSCoW | Soll |
| Abgedeckte Anforderungen | A-015, A-038 |
| Abhängigkeiten (Story-IDs) | US-000, US-005 |
| Welle | 6 |

## Story

Als **Operator** möchte ich **`service install`** (User-Domain, KeepAlive, Label `com.maccluster.heal`).

## Dateibesitz (exklusiv)

- `src/maccluster/adapters/launchagent.py`
- `src/maccluster/adapters/plist_template.py`
- `src/maccluster/services/service_mgmt.py`
- `src/maccluster/commands/service_cmd.py`
- `tests/unit/adapters/test_launchagent.py`
- `tests/unit/adapters/test_plist_template.py`
- `tests/unit/services/test_service_mgmt_install.py`
- `tests/unit/commands/test_service_cmd.py`

## Hinweise

- Modul implementiert install **und** uninstall/status; US-014/US-015 = Abnahmetests.
- Intra-Welle nach US-016 (`heal --loop` muss existieren).

---

# US-014 — Service uninstall

| Feld | Wert |
|---|---|
| ID | US-014 |
| MoSCoW | Soll |
| Abgedeckte Anforderungen | A-016 |
| Abhängigkeiten (Story-IDs) | US-000 |
| Welle | 6 |

## Story

Als **Operator** möchte ich **`service uninstall`** idempotent (Exit 0 auch ohne Installation).

## Dateibesitz (exklusiv)

- `tests/unit/services/test_service_mgmt_uninstall.py`
- `tests/integration/test_service_uninstall_idempotent.py`

---

# US-015 — Service status

| Feld | Wert |
|---|---|
| ID | US-015 |
| MoSCoW | Soll |
| Abgedeckte Anforderungen | A-017 |
| Abhängigkeiten (Story-IDs) | US-000 |
| Welle | 6 |

## Story

Als **Operator** möchte ich **`service status`** (installed/running/Label/Intervall) ohne Root.

## Dateibesitz (exklusiv)

- `tests/unit/services/test_service_mgmt_status.py`
- `tests/integration/test_service_status_readonly.py`

---

# US-018 — Bandwidth-Bench mit iperf3

| Feld | Wert |
|---|---|
| ID | US-018 |
| MoSCoW | Soll |
| Abgedeckte Anforderungen | A-025, A-045 |
| Abhängigkeiten (Story-IDs) | US-000, US-003, US-006 |
| Welle | 6 |

## Story

Als **Operator** möchte ich **`bench`** gegen Peer-IP wenn iperf3 vorhanden (Duration ≤ 5 s).

## Dateibesitz (exklusiv)

- `src/maccluster/adapters/iperf3.py`
- `src/maccluster/services/bench_service.py`
- `src/maccluster/commands/bench.py`
- `tests/unit/adapters/test_iperf3.py`
- `tests/unit/services/test_bench_service.py`
- `tests/unit/commands/test_bench.py`

---

# US-019 — Bench ohne iperf3 graceful

| Feld | Wert |
|---|---|
| ID | US-019 |
| MoSCoW | Soll |
| Abgedeckte Anforderungen | A-026 |
| Abhängigkeiten (Story-IDs) | US-000 |
| Welle | 6 |

## Story

Als **Operator** möchte ich **Exit 1 + „iperf3 not found“** ohne das restliche CLI zu brechen.

## Dateibesitz (exklusiv)

- `tests/unit/commands/test_bench_missing_iperf.py`
- `tests/integration/test_bench_graceful.py`

## Hinweise

- Intra-Welle nach US-018.

---

# US-021 — Optionale SSH-Peer-Probes

| Feld | Wert |
|---|---|
| ID | US-021 |
| MoSCoW | Soll |
| Abgedeckte Anforderungen | A-032, A-044, A-045 |
| Abhängigkeiten (Story-IDs) | US-000, US-006 |
| Welle | 6 |

## Story

Als **Operator** möchte ich **optionale SSH-Probes** (Default aus, BatchMode, Timeout 3 s).

## Dateibesitz (exklusiv)

- `src/maccluster/adapters/ssh_probe.py`
- `tests/unit/adapters/test_ssh_probe.py`
- `tests/unit/services/test_ssh_optional_status.py`

---

# US-022 — Action-Log mit Rotation (Kann)

| Feld | Wert |
|---|---|
| ID | US-022 |
| MoSCoW | Kann |
| Abgedeckte Anforderungen | A-036 |
| Abhängigkeiten (Story-IDs) | US-000, US-004 |
| Welle | 6 |

## Story

Als **Operator** möchte ich **optionales Action-Log** (Default aus, Rotation max 5 MiB).

## Dateibesitz (exklusiv)

- `src/maccluster/audit/log.py`
- `tests/unit/audit/test_log_rotation.py`

---

# US-023 — Optionale Rich-TUI Monitor (Kann)

| Feld | Wert |
|---|---|
| ID | US-023 |
| MoSCoW | Kann |
| Abgedeckte Anforderungen | A-037, A-021 |
| Abhängigkeiten (Story-IDs) | US-000, US-007 |
| Welle | 6 |

## Story

Als **Operator** möchte ich **optional rich** mit Plaintext-Fallback und NO_COLOR.

## Dateibesitz (exklusiv)

- `src/maccluster/render/rich_monitor.py`
- `tests/unit/render/test_rich_monitor.py`

---

# US-024 — Installation symmetrisch + Platform-Grenze

| Feld | Wert |
|---|---|
| ID | US-024 |
| MoSCoW | Muss |
| Abgedeckte Anforderungen | A-034, A-043 |
| Abhängigkeiten (Story-IDs) | US-000 |
| Welle | 6 |

## Story

Als **Operator** möchte ich **identische Installation auf jedem Member** und klare **Platform-Grenze** (macOS Apple Silicon Mac mini).

## Dateibesitz (exklusiv)

- `docs/faq/.gitkeep`
- `tests/integration/test_platform_guard_cli.py`
- `tests/integration/test_install_entrypoints.py`

## Hinweise

- README Install/Platform per Additivrecht. FAQ-Inhalte final in Abnahme.

---

# US-025 — Offline-Betrieb ohne Cloud

| Feld | Wert |
|---|---|
| ID | US-025 |
| MoSCoW | Muss |
| Abgedeckte Anforderungen | A-035 |
| Abhängigkeiten (Story-IDs) | US-000 |
| Welle | 6 |

## Story

Als **Operator** möchte ich **Kernfunktionen ohne Internet/Cloud** (kein Login, keine Cloud-Clients).

## Dateibesitz (exklusiv)

- `tests/integration/test_offline_no_cloud_imports.py`
- `tests/unit/test_no_network_clients.py`

---

## Planungskonflikte

**Keine ungelösten Konflikte.**

| Hotspot | Lösung |
|---|---|
| `cli/parser.py` | US-000 exklusiv |
| `app_factory.py` | US-000 + Adapter-Additivrecht |
| `commands/heal.py` | US-005; US-016 nur `--loop` |
| Shared Ensure | US-004 (`mutate_service`, `heal_logic`) |
| service * | US-013 Code; US-014/015 Tests |
| `tests/fixtures/topology/` | US-008 (inkl. partial_mesh) |

## ANNAHMEN

| ID | Annahme |
|---|---|
| P-1 | 6 Wellen Vollausbau |
| P-2 | US-000 Gerüst-Paket |
| P-3 | Intra-Wellen-Reihenfolge für enge Kopplung; formale abhaengigkeiten nur wellen-rückwärts |
| P-4 | A-031 → US-004 |
| P-5 | FAQ-Inhalte in Abnahme |
| P-6 | partial_mesh-Fixture → US-008 |
