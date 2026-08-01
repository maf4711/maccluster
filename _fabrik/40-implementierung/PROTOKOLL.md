# Implementierungsprotokoll — MacCluster

| Feld | Wert |
|---|---|
| Projekt | maccluster |
| Phase | 4 IMPLEMENTIERUNG |
| Stand | 2026-08-01 |
| Wellen | 1–6 (Vollausbau in einem Durchlauf) |
| Status | **fertig** (Tests grün) |

## 1. Umgesetzt

Vollständiges Python-CLI-Package `maccluster` 0.1.0 unter `projects/maccluster/`
(nicht unter `_fabrik/`), gemäß `ARCHITEKTUR.md` / `STACK.md` / Wellen 1–6.

### 1.1 Gerüst (Welle 1)

- `pyproject.toml` (hatchling, Python ≥3.11, entry `maccluster=maccluster.cli.main:main`)
- `LICENSE` (MIT), `README.md` (EN), `CHANGELOG.md`, `Makefile`, `install.sh`, `.gitignore`
- `.github/workflows/ci.yml` (pytest + ruff), `.github/dependabot.yml`
- `requirements-dev.txt`, `requirements-lock.txt`
- Package-Layout `src/maccluster/` mit Ports, Adapters, Domain, CLI

### 1.2 Config / Init (Welle 2)

- TOML Config Schema v1, Default-Pfad `~/.config/maccluster/cluster.toml`
- Override: `--config` > `MACCLUSTER_CONFIG` > Default
- `init`, `config show|validate`, Self-Match Hostname/HW-UUID
- `examples/cluster.toml` (4 Nodes, 10.42.0.1–.4)
- Atomic write 0600, Symlink-Policy, Backup bei `--force`

### 1.3 Thunderbolt / Render (Welle 3)

- `tb`: system_profiler primär, ioreg Fallback
- Fixture: `tests/fixtures/system_profiler/sample_m4_mini.txt` (Live-Capture dieses Macs)
- Receptacle-Mapping pure + `docs/receptacle-mapping.md`
- Plaintext-Symbole `[UP]`/`[DOWN]`/`[LINK]`/`[NO-LINK]`; `NO_COLOR`

### 1.4 Mutate up/heal (Welle 4)

- Shared Ensure-Pfad `services/mutate_service.py` + `heal_logic/plan.py`
- File-Lock, Network read/apply split, Privilege-Meldung `admin/sudo required`
- Exit 3 bei Bridge/IP ok aber kein TB-Link
- Platform-Guard macOS+arm64 (`MACCLUSTER_SKIP_PLATFORM_GUARD=1` für Tests/CI)

### 1.5 Status / Monitor / Topo / JSON (Welle 5)

- `status`, `monitor` (Interval, Ctrl+C → 0), `topo` (ohne Kabelführungs-Empfehlung)
- `--json` mit `schema_version`
- Exit 3 bei Peer-down (Self ok)

### 1.6 Doctor / Service / Bench / Kann (Welle 6)

- `doctor` (Config, Self, TB, Bridge, Peers, optional iperf3-Info)
- `heal --loop` best-effort
- `service install|uninstall|status` User-LaunchAgent `com.maccluster.heal`
- `bench` mit iperf3; Exit 1 + Hinweis wenn fehlend
- Optional SSH-Probes, Audit-Log mit Rotation, optional rich Monitor

## 2. Build & Tests

| Kommando | Ergebnis |
|---|---|
| `python3 -m pip install -e ".[dev]"` | ok |
| `python3 -m pytest -q` | **93 passed** |
| `ruff check src tests` | All checks passed |
| `ruff format --check src tests` | formatted |
| `python3 -m maccluster --help` | ok (alle Subcommands) |
| `python3 -m maccluster tb` | ok — 3 Receptacles, unconnected, 120 Gb/s (Live Mac mini) |
| `maccluster --version` | 0.1.0 |

## 3. Akzeptanzkriterien (Kurz)

| Gruppe | A-IDs | Abdeckung |
|---|---|---|
| TB | A-001, A-002, A-039 | `tb` + Mapping + Fixture-Tests |
| Config/init | A-003–A-008, A-027, A-040, A-042 | init/validate/example/missing |
| up/heal | A-009–A-014, A-038, A-041 | Fake-Apply, Privilege, Degraded no-link |
| status/monitor | A-018–A-021 | Exit 0/3, Symbole, NO_COLOR |
| topo | A-022, A-023 | Map ohne Rewire-Advice |
| doctor/bench | A-024–A-026 | Report-Exit, iperf missing |
| service | A-015–A-017 | Fake LaunchAgent install/uninstall/status |
| Robustheit | A-028–A-031, A-043–A-045 | Guard, Lock, ProcessRunner allowlist |
| Offline | A-034, A-035 | Keine Cloud-Imports |

## 4. Bekannte Lücken / Annahmen

1. **Live-4-Node-Mesh** nicht in CI; Mutationen über Fake-Adapter. Manuelle Abnahme auf Flotte nötig (Gate 4).
2. **User-LaunchAgent ohne Root** kann Bridge nach Reboot nicht setzen, wenn OS Root verlangt — dokumentiert (best-effort, README).
3. **ifconfig IP-Set** ist macOS-best-effort; Alias-Formen können je Version variieren.
4. **SSH-Probes** Default aus; StrictHostKeyChecking=accept-new (kein `no`).
5. **Rich-TUI** optional; Kern ohne `rich` vollständig.
6. FAQ-Inhalte `docs/faq/{USER,ADMIN,...}.md` nur Platzhalter-`.gitkeep` — Abnahme-Phase kann ausfüllen.
7. Keine echten Secrets in Fixtures; HW-UUIDs in Beispiel-Config sind Platzhalter.

## 5. Dateibaum (Produkt, Auszug)

```
projects/maccluster/
├── LICENSE, README.md, CHANGELOG.md, pyproject.toml, Makefile, install.sh
├── examples/cluster.toml
├── docs/receptacle-mapping.md
├── .github/workflows/ci.yml, dependabot.yml
├── src/maccluster/   # cli, commands, services, domain, config, adapters, …
└── tests/            # unit + integration + fixtures
```

## 6. state.json

`wellen.fertig` → **6** nach grünem Gesamttestlauf.
