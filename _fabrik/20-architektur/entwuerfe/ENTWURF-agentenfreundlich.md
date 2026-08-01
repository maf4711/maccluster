# ENTWURF — Agentenfreundlich (A)

| Feld | Wert |
|---|---|
| Projekt | MacCluster (`maccluster`) |
| Entwurf-ID | **A — Agentenfreundlich** |
| Phase | 2 ARCHITEKTUR |
| Stand | 2026-08-01 |
| Quelle | Brief, `10-analyse/*` (AD-1…AD-6, A-001…A-045, NFA) |
| Leitlinie | Viele parallele KI-Agenten: strenge Konventionen, kleine Module, klarer Dateibesitz, Testbarkeit, keine implizite Magie |
| Stack-Skizze | Python 3.11+ · stdlib primär · optional `rich` (extra) |

Dieser Entwurf ist ein **ernsthafter Architekturalternativ-Kandidat**. Er priorisiert
**Implementierbarkeit in disjunkten Wellen** über Framework-Eleganz.

---

## 1. Architekturprinzipien (bindend für diesen Entwurf)

| # | Prinzip | Konkretisierung |
|---|---|---|
| P1 | **Dateischarfe Module** | Ein Modul = ein Verantwortungsbereich = ein Primärdatei-Cluster. Keine „God-Files“. |
| P2 | **Explizite I/O-Grenzen** | Alle OS-Aufrufe nur über `probes/runner.py` + typisierte Ports. Kein `subprocess` in Domain/CLI/Render. |
| P3 | **Keine Magie** | Kein Plugin-Autodiscovery, kein metaclass-Registry, kein globaler Singleton-State, kein implizites Import-Side-Effect. Entry: `cli/main.py` → Command-Handler → Services. |
| P4 | **Pure Core** | Domain-Modelle, Validierung, Topo-Match, Aggregationen, Mapping-Parser: pure Funktionen / dataclasses, 100 % fixture-testbar. |
| P5 | **Dependency Injection light** | Handler erhalten ein `AppContext` (dataclass) mit Ports; Tests injizieren Fakes. Kein DI-Framework. |
| P6 | **Ein Package, symmetrisch** | Ein Binary `maccluster` auf jedem Member; Self/Peer nur aus Identity-Match. |
| P7 | **Fail closed bei Mutation** | Unklares Mapping, invalide Config, unsupported platform → Exit 2, keine Netzänderung. |
| P8 | **Konvention vor Framework** | Namens-, Exit-, JSON-, Error-Contracts in diesem Dokument; Agenten folgen Tabellen, nicht „implizitem Stil“. |

---

## 2. Komponentenschnitt

### 2.1 Schichten (strikt, keine Aufwärts-Imports)

```
┌─────────────────────────────────────────────────────────────┐
│  cli/          argparse, Exit-Codes, Command-Dispatch       │
├─────────────────────────────────────────────────────────────┤
│  commands/     ein Handler pro Subcommand (dünn)            │
├─────────────────────────────────────────────────────────────┤
│  services/     Orchestrierung: status, up, heal, doctor…    │
├───────────────┬─────────────────┬───────────────────────────┤
│  config/      │  domain/        │  render/                  │
│  load/valid   │  models/inv     │  plain / json / rich      │
├───────────────┴─────────────────┴───────────────────────────┤
│  mapping/ · topology/ · health/ · heal_logic/ · doctor_logic│
│  (pure / semi-pure, fixture-first)                          │
├─────────────────────────────────────────────────────────────┤
│  ports/        Protocols (typing.Protocol) — nur Interfaces │
├─────────────────────────────────────────────────────────────┤
│  adapters/     macOS: probes, network apply, launchctl, ssh │
│  (einzige Stelle mit subprocess / Dateisystem-Mutation OS)  │
└─────────────────────────────────────────────────────────────┘
```

**Importregel (hart):**

- `domain`, `config` (parse/validate), `mapping`, `topology`, `health` → **keine** Imports aus `adapters`, `cli`, `commands`.
- `adapters` → darf `domain`-Typen und `ports` nutzen; **kein** Import aus `commands`/`render`.
- `commands` → `services` + `render` + `cli.exit_codes`; **kein** direkter `subprocess`.
- Zyklen verboten; CI-Check optional via `import-linter` **nicht** Pflicht (Konvention + Review genügt v1).

### 2.2 Komponenten-Verantwortlichkeiten

| Komponente | Verantwortung | Nicht-Verantwortung |
|---|---|---|
| **cli** | Argparse-Baum, globale Flags (`--config`, `--json`, `-v`, `NO_COLOR`), Exit-Mapping | Business-Logik |
| **commands** | Pro Subcommand: Args → Service-Aufruf → Render → Exit | OS-Probes |
| **services** | Use-Cases orchestrieren (Config laden, Probes, Aggregate, Apply) | Parsing von system_profiler-Rohtext |
| **domain** | Dataclasses, Enums, Invarianten-IDs | I/O |
| **config** | Pfadauflösung AD-6, TOML load/dump, Validierung A-005…A-007, A-042 | Network apply |
| **mapping** | Receptacle→Interface (A-039), pure + fixtures | ifconfig schreiben |
| **topology** | Links matchen, Konfidenz, unmatched (A-022/A-023) | Kabelführungs-Empfehlung |
| **health** | Snapshot bauen, overall_status, Exit-3-Semantik | Monitor-Loop-UI |
| **heal_logic** | Drift erkennen → geordnete `HealAction`-Liste (pure Plan) | Ausführen der Actions |
| **doctor_logic** | Check-Liste, Severity-Aggregation (A-X2) | Rendering |
| **ports** | `Protocol`-Schnittstellen | Implementierung |
| **adapters** | Runner, TB-Probes, NetworkApply, LaunchAgent, Ping, SSH, iperf3 | Domain-Regeln |
| **render** | Plaintext, JSON (`schema_version`), optional rich | Daten beschaffen |
| **platform** | Apple Silicon / macOS Guard (A-043), Self-Identity | — |
| **lock** | Single-Writer für up/heal (A-031) | Read-only-Serialisierung |
| **audit** | Optionales Action-Log + Rotation (Kann) | Pflicht-Persistenz |

### 2.3 AppContext (einziger „Kleber“)

```python
# Conceptual sketch (English identifiers in product code)
@dataclass(frozen=True)
class AppContext:
    config_path: Path
    json_mode: bool
    verbose: bool
    no_color: bool
    clock: ClockPort
    fs: FileSystemPort
    runner: ProcessRunnerPort
    tb: ThunderboltProbePort
    net_read: NetworkReadPort
    net_apply: NetworkApplyPort
    reachability: ReachabilityPort
    service: ServicePort
    bench: BenchPort | None
    lock: LockPort
    identity: IdentityPort
    platform: PlatformPort
```

- Produktion: `AppContext.production()` in `app_factory.py` (eine Datei).
- Tests: `AppContext` mit Fake-Ports; **kein** Monkeypatch von `subprocess` quer durchs Repo.

---

## 3. Datenfluss

### 3.1 Read-only (status / monitor / topo / tb / doctor)

```
Operator
  → cli.main
  → commands.status (o.ä.)
  → services.status_service
       → config.load + validate
       → platform.identity → self Node
       → adapters.tb.probe / net_read / ping
       → health.build_snapshot / topology.build
  → render.plain | render.json
  → exit_codes.from_health(snapshot)   # 0 | 3 | 1 | 2
```

### 3.2 Mutierend (up / heal)

```
Operator
  → commands.up | commands.heal
  → services.mutate_service
       → platform.guard_mutate()          # A-043 → Exit 2
       → config.load + validate           # Exit 2
       → lock.acquire()                   # A-031
       → mapping.resolve_interface()      # Ambiguität → Exit 2 (A-039)
       → heal_logic.plan(desired, observed)  # pure
       → adapters.net_apply.execute(actions) # only self host (A-041)
       → optional audit.append
       → lock.release()
  → render result
  → exit: 0 ok | 3 no TB link (A-011) | 1 privilege/runtime | 2 usage
```

### 3.3 Service (LaunchAgent)

```
service install
  → generate plist (pure template in service/plist_template.py)
  → write ~/Library/LaunchAgents/…
  → launchctl bootstrap gui/$(id -u)   # AD-4
  → status readback
```

Heal-Loop im Agent: `maccluster heal --loop` (gleiches Binary, gleiche Config-Pfad-Resolution).

### 3.4 Persistenz

| Artefakt | Pfad (Default) | Schreibende Komponente |
|---|---|---|
| ClusterConfig | `~/.config/maccluster/cluster.toml` | `config/init.py`, Operator manuell |
| Writer-Lock | `~/.config/maccluster/maccluster.lock` | `adapters/lock_file.py` |
| LaunchAgent Plist | `~/Library/LaunchAgents/com.maccluster.heal.plist` | `adapters/launchagent.py` |
| Action-Log (opt.) | `~/.local/state/maccluster/actions.log` | `audit/log.py` |

Keine DB. Secrets nie in Dateien des Tools speichern.

---

## 4. Stack-Skizze

| Schicht | Wahl | Version / Hinweis |
|---|---|---|
| Sprache | Python | **3.11+** (Brief G, NFA-041) |
| Packaging | `pyproject.toml` + hatchling oder setuptools | Entry: `maccluster = maccluster.cli.main:main` |
| CLI | `argparse` (stdlib) | Kein click/typer (weniger Magic, eine weniger Dep) |
| Config | `tomllib` (3.11+) lesen; Schreiben: stdlib `tomllib` + manuelles Dump **oder** minimales eigenes TOML-Writer für bekannte Struktur | Kein pydantic |
| Modelle | `dataclasses` + `enum` + `typing` | Kein ORM |
| Subprocess | `subprocess.run(..., shell=False, timeout=…)` nur in `adapters/process.py` | A-044 / NFA-022 |
| JSON | `json` stdlib | `schema_version` Pflicht in Outputs |
| Terminal | Plaintext default; optional **`rich`** als extra `[monitor]` | A-021, A-037 |
| Tests | `unittest` stdlib **oder** `pytest` (eine Test-Runner-Wahl in STACK.md) | Fixtures unter `tests/fixtures/` |
| Lint | `ruff` in CI | NFA-044 |
| SCA | Dependabot + optional pip-audit | NFA-025 |

**Abhängigkeiten Runtime:**

- **Required:** keine (pure stdlib), *oder* nur Packaging-Meta.
- **Optional:** `rich` via `pip install 'maccluster[monitor]'`.

**Nicht im Stack:** HTTP-Server, asyncio-Framework, ORM, cloud SDKs, Ollama/OpenAI.

---

## 5. Vollständiger Verzeichnisbaum mit Dateibesitz

Legende Besitz:

- **Owner-Tag** = geplante Story-/Wellen-Einheit (Planung füllt finale US-IDs).
- Innerhalb einer Welle müssen Owner-Tags **disjunkt** bleiben.
- `shared:` = nur Welle 1 / Gerüst; danach stabil, nur mit Review anfassen.
- Tests liegen **neben** dem Modul-Owner (gleicher Tag), nicht in einem globalen „tests-Gott“.

```
maccluster/                          # Produkt-Repo-Root (projects/maccluster/)
├── LICENSE                          # shared: W1 Gerüst (G1)
├── README.md                        # shared: W1 + Doku-Welle (Install, Exit-Codes, Mapping)
├── CHANGELOG.md                     # shared: fortlaufend Abnahme
├── pyproject.toml                   # shared: W1 Packaging, entry points, optional rich
├── requirements-dev.txt             # shared: W1 (ruff, pytest falls gewählt)
├── install.sh                       # shared: W1 A-034
├── .gitignore                       # shared: W1
├── .github/
│   ├── workflows/
│   │   └── ci.yml                   # shared: W1 lint + unit (NFA-044)
│   └── dependabot.yml               # shared: W1 (gen_dependabot / manuell)
│
├── examples/
│   └── cluster.toml                 # owner: config-examples  (A-008, US-026)
│
├── docs/                            # optional; kann schlank bleiben
│   └── receptacle-mapping.md        # owner: mapping-docs  (A-039)
│
├── src/
│   └── maccluster/
│       ├── __init__.py              # shared: version string only
│       ├── __main__.py              # shared: python -m maccluster
│       ├── app_factory.py           # shared: AppContext.production() — dünn, spät
│       │
│       ├── cli/
│       │   ├── __init__.py
│       │   ├── main.py              # owner: cli-core     Entry + dispatch table
│       │   ├── parser.py            # owner: cli-core     argparse tree only
│       │   ├── exit_codes.py        # owner: cli-core     0/1/2/3 constants + helpers (AD-3)
│       │   └── errors.py            # owner: cli-core     CliError(code, message, payload)
│       │
│       ├── commands/                # je Datei = ein Subcommand (dünn, < ~80 LOC Ziel)
│       │   ├── __init__.py
│       │   ├── tb.py                # owner: cmd-tb
│       │   ├── init_cmd.py          # owner: cmd-init     (init is keyword-ish)
│       │   ├── config_cmd.py        # owner: cmd-config   show | validate
│       │   ├── up.py                # owner: cmd-up
│       │   ├── heal.py              # owner: cmd-heal
│       │   ├── status.py            # owner: cmd-status
│       │   ├── monitor.py           # owner: cmd-monitor
│       │   ├── topo.py              # owner: cmd-topo
│       │   ├── doctor.py            # owner: cmd-doctor
│       │   ├── bench.py             # owner: cmd-bench
│       │   └── service.py           # owner: cmd-service  install|uninstall|status
│       │
│       ├── services/                # Orchestrierung (kein argparse, kein print-Rohformat)
│       │   ├── __init__.py
│       │   ├── tb_service.py        # owner: svc-tb
│       │   ├── init_service.py      # owner: svc-init
│       │   ├── config_service.py    # owner: svc-config
│       │   ├── up_service.py        # owner: svc-up
│       │   ├── heal_service.py      # owner: svc-heal
│       │   ├── status_service.py    # owner: svc-status
│       │   ├── monitor_service.py   # owner: svc-monitor  loop + refresh budget
│       │   ├── topo_service.py      # owner: svc-topo
│       │   ├── doctor_service.py    # owner: svc-doctor
│       │   ├── bench_service.py     # owner: svc-bench
│       │   └── service_mgmt.py      # owner: svc-launchd
│       │
│       ├── domain/
│       │   ├── __init__.py
│       │   ├── models.py            # owner: domain-models   ClusterConfig, Node, …
│       │   ├── enums.py             # owner: domain-models   Role, Severity, OverallStatus, …
│       │   └── invariants.py        # owner: domain-models   INV checks as pure functions
│       │
│       ├── config/
│       │   ├── __init__.py
│       │   ├── paths.py             # owner: config-core   AD-6 resolution CLI>ENV>default
│       │   ├── schema.py            # owner: config-core   schema_version = 1 constants
│       │   ├── load.py              # owner: config-core   TOML → ClusterConfig
│       │   ├── validate.py          # owner: config-core   A-005…A-007, A-006, A-042
│       │   ├── dump.py              # owner: config-core   ClusterConfig → TOML text
│       │   └── init_template.py     # owner: config-init   template builders for init
│       │
│       ├── platform/
│       │   ├── __init__.py
│       │   ├── guard.py             # owner: platform   A-043 mutate vs read-only
│       │   └── identity.py          # owner: platform   hostname + HW-UUID read (via port)
│       │
│       ├── mapping/
│       │   ├── __init__.py
│       │   ├── receptacle.py        # owner: mapping   pure map + ambiguity fail-closed
│       │   └── layouts.py           # owner: mapping   known mini layout tables (data)
│       │
│       ├── topology/
│       │   ├── __init__.py
│       │   ├── match.py             # owner: topology   peer match + confidence
│       │   └── build.py             # owner: topology   Topology from ports+links+config
│       │
│       ├── health/
│       │   ├── __init__.py
│       │   ├── snapshot.py          # owner: health    build HealthSnapshot
│       │   └── aggregate.py         # owner: health    overall_status + exit hint
│       │
│       ├── heal_logic/
│       │   ├── __init__.py
│       │   ├── plan.py              # owner: heal-logic  observed vs desired → HealAction[]
│       │   └── idempotency.py       # owner: heal-logic  already-configured detection
│       │
│       ├── doctor_logic/
│       │   ├── __init__.py
│       │   ├── checks.py            # owner: doctor-logic  individual check functions
│       │   └── report.py            # owner: doctor-logic  aggregate findings + exit (A-X2)
│       │
│       ├── ports/
│       │   ├── __init__.py
│       │   ├── clock.py             # owner: ports   Protocol only
│       │   ├── filesystem.py        # owner: ports
│       │   ├── process.py           # owner: ports
│       │   ├── thunderbolt.py       # owner: ports
│       │   ├── network.py           # owner: ports   read + apply split
│       │   ├── reachability.py      # owner: ports
│       │   ├── service.py           # owner: ports
│       │   ├── bench.py             # owner: ports
│       │   ├── lock.py              # owner: ports
│       │   ├── identity.py          # owner: ports
│       │   └── platform.py          # owner: ports
│       │
│       ├── adapters/
│       │   ├── __init__.py
│       │   ├── process.py           # owner: adp-process   ONLY subprocess entry (timeouts)
│       │   ├── filesystem.py        # owner: adp-fs        atomic write, 0600, symlink policy
│       │   ├── clock.py             # owner: adp-clock
│       │   ├── identity_macos.py    # owner: adp-identity  scutil/system_profiler UUID
│       │   ├── platform_macos.py    # owner: adp-platform
│       │   ├── tb_system_profiler.py# owner: adp-tb-sp     parse SPThunderboltDataType
│       │   ├── tb_ioreg.py          # owner: adp-tb-ioreg  fallback chain
│       │   ├── network_read.py      # owner: adp-net-read  ifconfig parse (read-only)
│       │   ├── network_apply.py     # owner: adp-net-apply ifconfig/networksetup allowlist
│       │   ├── ping_macos.py        # owner: adp-ping      macOS ping flags + timeout
│       │   ├── ssh_probe.py         # owner: adp-ssh       BatchMode, optional (AD-2)
│       │   ├── lock_file.py         # owner: adp-lock      PID+stale (RF-A20)
│       │   ├── launchagent.py       # owner: adp-launchd  bootstrap/bootout/status
│       │   ├── plist_template.py    # owner: adp-launchd  XML template pure-ish
│       │   └── iperf3.py            # owner: adp-iperf
│       │
│       ├── render/
│       │   ├── __init__.py
│       │   ├── symbols.py           # owner: render-core   UP/DOWN symbols (no color-only)
│       │   ├── plain.py             # owner: render-plain  all human tables
│       │   ├── json_out.py          # owner: render-json   schema_version envelopes
│       │   ├── sanitize.py          # owner: render-core   ANSI strip hostnames (RF-F5-20)
│       │   └── rich_monitor.py      # owner: render-rich   optional; import guarded
│       │
│       └── audit/
│           ├── __init__.py
│           └── log.py               # owner: audit   optional rotate 5 MiB (A-036)
│
└── tests/
    ├── fixtures/
    │   ├── system_profiler/         # owner: adp-tb-sp + mapping (shared fixtures)
    │   │   ├── mini_tb4_2port.xml
    │   │   ├── mini_no_link.json
    │   │   └── truncated_garbage.txt
    │   ├── ioreg/
    │   │   └── sample_tb.txt
    │   ├── ifconfig/
    │   │   ├── bridge_ok.txt
    │   │   └── bridge_missing_ip.txt
    │   ├── configs/
    │   │   ├── valid_2node.toml
    │   │   ├── valid_4node.toml
    │   │   ├── invalid_dup_ip.toml
    │   │   ├── invalid_1node.toml
    │   │   ├── invalid_5node.toml
    │   │   └── missing_schema.toml
    │   └── topology/
    │       ├── line_2node.json
    │       └── partial_mesh.json
    │
    ├── unit/
    │   ├── domain/
    │   │   └── test_invariants.py   # owner: domain-models
    │   ├── config/
    │   │   ├── test_paths.py        # owner: config-core
    │   │   ├── test_load.py         # owner: config-core
    │   │   ├── test_validate.py     # owner: config-core
    │   │   └── test_init_template.py # owner: config-init
    │   ├── mapping/
    │   │   └── test_receptacle.py   # owner: mapping
    │   ├── topology/
    │   │   ├── test_match.py        # owner: topology
    │   │   └── test_build.py        # owner: topology
    │   ├── health/
    │   │   ├── test_snapshot.py     # owner: health
    │   │   └── test_aggregate.py    # owner: health
    │   ├── heal_logic/
    │   │   ├── test_plan.py         # owner: heal-logic
    │   │   └── test_idempotency.py  # owner: heal-logic
    │   ├── doctor_logic/
    │   │   ├── test_checks.py       # owner: doctor-logic
    │   │   └── test_report.py       # owner: doctor-logic
    │   ├── platform/
    │   │   └── test_guard.py        # owner: platform
    │   ├── render/
    │   │   ├── test_plain.py        # owner: render-plain
    │   │   ├── test_json_out.py     # owner: render-json
    │   │   └── test_sanitize.py     # owner: render-core
    │   ├── adapters/
    │   │   ├── test_process_argv.py # owner: adp-process  no shell=True, special chars
    │   │   ├── test_tb_profiler.py  # owner: adp-tb-sp
    │   │   ├── test_tb_ioreg.py     # owner: adp-tb-ioreg
    │   │   ├── test_network_read.py # owner: adp-net-read
    │   │   ├── test_ping_flags.py   # owner: adp-ping
    │   │   ├── test_lock_stale.py   # owner: adp-lock
    │   │   └── test_plist_template.py # owner: adp-launchd
    │   ├── cli/
    │   │   └── test_exit_codes.py   # owner: cli-core
    │   └── services/
    │       ├── test_up_service.py   # owner: svc-up   fakes only
    │       ├── test_heal_service.py # owner: svc-heal
    │       └── test_status_service.py # owner: svc-status
    │
    └── integration/
        ├── test_cli_help.py         # owner: cli-core
        ├── test_init_roundtrip.py   # owner: svc-init + config
        ├── test_status_json_schema.py # owner: render-json + svc-status
        ├── test_doctor_exit.py      # owner: svc-doctor
        └── test_mutate_guard_linux.py # owner: platform  skipif darwin if needed
```

### 5.1 Owner-Tag → Anforderungs-Cluster (Planungshilfe)

| Owner-Tag | Primäre A-IDs / Stories | Wellen-Hinweis |
|---|---|---|
| shared / W1 | Gerüst G1–G5, Packaging, CI | Welle 1 allein |
| domain-models | Domänenabbildung | früh, vor Services |
| config-core | A-005…A-007, A-027, A-040, A-042 | früh |
| config-init | A-003, A-004 | nach config-core |
| platform | A-043, Identity für A-007 | früh |
| mapping | A-039 | vor up/heal |
| adp-process | A-044, A-045 Timeouts | vor allen Adaptern |
| adp-tb-sp / ioreg | A-001, A-002 | parallel zu mapping |
| adp-net-read / apply | A-009…A-012, A-041 | nach mapping + lock |
| adp-lock | A-031 | vor mutate services |
| heal-logic + svc-up/heal | A-009…A-014, A-038 | nach apply |
| health + svc-status/monitor | A-018…A-021 | parallel zu topo möglich |
| topology | A-022, A-023 | parallel health |
| doctor-logic | A-024, A-026 skip | nach probes |
| adp-launchd + svc-launchd | A-015…A-017 | nach heal |
| adp-ssh / adp-iperf | A-032, A-025 | spät, optional |
| render-* | A-021, A-033, A-037 | parallel zu services |
| audit | A-036 Kann | zuletzt |

### 5.2 Parallelitäts-Regeln für Agenten

1. **Ein Agent = ein Owner-Tag** pro Task; Diff darf nur Dateien dieses Tags + zugehörige `tests/…` berühren.
2. **Ports zuerst:** `ports/*.py` werden in Welle 1/2 stabilisiert; Adapter und Services hängen daran.
3. **Keine Cross-Edits:** Braucht Service X eine Änderung an `domain/models.py`, separate Story mit Owner `domain-models` — kein „mitgemacht“.
4. **Fixture-First:** Parser-Stories liefern Fixture + Test **vor** oder **mit** Parser-Code.
5. **Öffentliche Funktionen** pro Modul max. bewusst klein; Prefer `def build_x(...)` pure APIs.

---

## 6. Schnittstellen-Verträge (agentenstabil)

### 6.1 Exit-Codes (AD-3) — `cli/exit_codes.py`

| Code | Name | Verwendung |
|---|---|---|
| 0 | `OK` | Erfolg; Monitor Ctrl+C; doctor nur info-warns |
| 1 | `ERROR` | Runtime, Rechte, Probe-Crash, iperf3 missing (bench) |
| 2 | `USAGE` | Args, Config, Validation, unsupported mutate platform, mapping ambiguity |
| 3 | `DEGRADED` | Peer down (status); up without TB link; doctor warn-cluster |

### 6.2 CliError

```text
CliError(exit_code: int, message: str, details: dict | None)
```

- `main.py` fängt **nur** `CliError` und generische Exception (→ Exit 1, kein Traceback default).
- Services werfen `CliError`, printen nicht.

### 6.3 JSON-Envelope (A-033)

```json
{
  "schema_version": 1,
  "command": "status",
  "ok": true,
  "timestamp": "2026-08-01T12:00:00Z",
  "data": { }
}
```

Fehler:

```json
{
  "schema_version": 1,
  "command": "status",
  "ok": false,
  "error": { "code": "E_CONFIG", "message": "..." },
  "timestamp": "..."
}
```

### 6.4 ProcessRunner-Vertrag

```text
run(argv: list[str], timeout: float, env: dict | None) -> CompletedProc
# shell ALWAYS False
# argv[0] must be absolute or basename from allowlist
```

Allowlist (Beispiel): `system_profiler`, `ioreg`, `ifconfig`, `networksetup`, `ping`, `launchctl`, `sw_vers`, `sysctl`, `iperf3`, `ssh`, `scutil`.

### 6.5 NetworkApply-Allowlist (R-T04 / R-D02)

- Nur Interfaces, die Mapping/Config als TB/Bridge markiert.
- Verbotene Operationen: Default-Route, DNS global, Wi-Fi Power, fremde `en0`-IPs.
- Jede Apply-Funktion: `dry_run: bool` Parameter für Tests.

---

## 7. Domain-Abbildung (kurz)

| Domänenentität | Modul |
|---|---|
| `ClusterConfig`, `Node` | `domain/models.py` + `config/*` |
| `ThunderboltPort`, `ThunderboltLink` | `domain/models.py` + `adapters/tb_*` |
| `BridgeInterface` | `domain/models.py` + `adapters/network_*` |
| `Topology` | `topology/*` |
| `HealthSnapshot`, `ReachabilityCheck` | `health/*` |
| `HealAction` | `heal_logic/*` + apply |
| `ServiceState` | `adapters/launchagent.py` |
| `DoctorFinding` | `doctor_logic/*` |
| `BenchResult` | `adapters/iperf3.py` + service |
| `AuditEntry` | `audit/log.py` |

Defaults aus Analyse: Subnetz `10.42.0.0/24` (AD-1), SSH default off (AD-2), Config-Pfad AD-6, LaunchAgent User-Domain AD-4.

**Topology.complete (OP-7):**  
`complete = true` gdw. jeder Config-Peer **entweder** per Ping erreichbar **oder** per Domain/Link-Match zuordenbar ist. Kein Vollmesh-Kabelzwang.

---

## 8. Fehler- und Konfigurationsstrategie

| Thema | Strategie |
|---|---|
| Config-Pfad | `--config` > `MACCLUSTER_CONFIG` > `~/.config/maccluster/cluster.toml` |
| Secrets | Nur Env/OS-Keychain des Operators für SSH; Tool speichert keine Secrets |
| Privilegien | Preflight in `net_apply` / `launchagent`; Message `admin/sudo required`; Exit 1 |
| Timeouts | process default 15 s (RF-A8); ping ≤ 2 s; SSH 3 s; iperf3 ≤ 5 s test / hard cap |
| Partial up | Liste ok/failed steps; nie Exit 0 bei hard fail; Exit 3 nur AD-5-Fall |
| Logging | stderr für Human-Diagnose; optional audit file; verbose `-v` |
| NO_COLOR | `render` liest Env; keine ANSI wenn gesetzt |

---

## 9. Teststrategie (NFA-048/049)

| Ebene | Was | Wo |
|---|---|---|
| Unit pure | validate, mapping, topo match, heal plan, aggregate | `tests/unit/**` |
| Unit adapter | Parser gegen Fixtures, argv safety | `tests/unit/adapters/**` |
| Integration | CLI mit Fake-Context / temp HOME | `tests/integration/**` |
| Manuell Abnahme | Live Mac mini 2–4 Nodes, Reboot A-038 | `_fabrik/60-abnahme` |

**CI:** kein Live-TB; Fixtures reichen für Grün.

**Verify-Befehl:** `make verify` oder `python -m pytest && ruff check src tests` (W1 festlegen).

---

## 10. Stärken

1. **Maximale Parallelität:** Owner-Tags und dünne Command/Service-Dateien erlauben viele Agenten ohne Merge-Kollisionen.
2. **Hohe Testbarkeit:** Pure Core + Ports; CI ohne Hardware (NFA-048).
3. **Sicherheits-Baseline:** Ein Subprocess-Tor, Allowlists, fail-closed Mutation (A-044, R-D02).
4. **Kein Framework-Ballast:** stdlib + optional rich — Scope und CVE-Fläche klein (NFA-025).
5. **Anforderungen 1:1 mappbar:** Jede A-ID hängt an wenigen Dateien (Traceability für QA).
6. **Symmetrie & Offline:** Ein Package, keine Cloud (A-034, A-035).

---

## 11. Schwächen

1. **Mehr Dateien / Boilerplate** als ein „flat script“-Entwurf — Onboarding-Kosten für Menschen, für Agenten aber vorteilhaft.
2. **Orchestrierungs-Duplikation:** services spiegeln commands; Disziplin nötig, Logik nicht in commands zu ziehen.
3. **AppContext-Wachstum:** Viele Ports → `app_factory.py` wird zentral; Risiko „Gott-Factory“ wenn unkontrolliert erweitert.
4. **Kein generisches Plugin-Modell:** Neue Probes = neue Dateien + Port-Erweiterung (bewusst, gegen Magie).
5. **TOML-Schreiben ohne Library:** Eigenes Dump für bekannte Struktur muss getestet werden (oder minimale erlaubte Dep — STACK-Entscheidung).

---

## 12. Risiken dieses Entwurfs

| Risiko | Bezug | Mitigation im Entwurf |
|---|---|---|
| Zu feine Module → Integrationslücken | R-T05 | Integrationstests mit Fake-Ports; Smoke `status`/`init` |
| Adapter-Drift system_profiler | R-F01, R-D01 | Dual-Source + Fixtures multi-sample |
| Mapping falsch | R-T01, A-039 | Isoliertes Modul + fail-closed |
| Heal-Races multi-host | R-F03 | Nur lokal mutieren; Idempotenz pure getestet |
| Writer-Races same host | A-031 | `lock_file` vor apply |
| Privilege silent fail | R-T02 | Preflight + Exit 1, structured error |
| Agent verletzt Importregeln | — | Review-Checkliste + kurze ARCHITECTURE note in README |
| LaunchAgent ohne Root für Bridge | OP-5 / R-T03 | User-Agent ruft `heal`; heal meldet klar wenn Admin fehlt; README |

---

## 13. Abdeckung Muss-Anforderungen (Check)

| Gruppe | A-IDs | Entwurf-Ort |
|---|---|---|
| TB-Info | A-001, A-002 | adp-tb-*, cmd/svc-tb, render |
| Config/init | A-003–A-007, A-040, A-042 | config/*, cmd-init |
| up/heal | A-009–A-014, A-038, A-041 | heal_logic, adp-net-apply, svc-up/heal |
| status/monitor | A-018–A-021 | health, svc-status/monitor, render |
| topo | A-022, A-023 | topology/* |
| doctor | A-024 | doctor_logic, svc-doctor |
| Robustheit | A-027–A-030, A-039, A-043, A-044 | config, platform, mapping, process |
| Install/Offline | A-034, A-035 | packaging, no network clients |
| Soll (Vollausbau) | A-015–A-017, A-025, A-026, A-031–A-033, A-045 | launchd, iperf, lock, json, timeouts |
| Kann | A-036, A-037 | audit, rich_monitor |

---

## 14. Konventionen für Implementierungs-Agenten (Kurzcheckliste)

```
[ ] Nur Dateien meines Owner-Tags (+ Tests)
[ ] Kein subprocess außerhalb adapters/process.py
[ ] Kein shell=True
[ ] Neue Domain-Felder nur in domain/models.py (eigene Story)
[ ] CLI-Messages Englisch
[ ] Exit-Code aus exit_codes.py, keine Magic Numbers verstreut
[ ] Mutationen: platform.guard → validate → lock → plan → apply
[ ] Tests: pure ohne Netzwerk; Fixtures für Parser
[ ] rich nur optional importlib.util.find_spec
[ ] Keine Secrets in examples/ oder Logs
```

---

## 15. ANNAHMEN dieses Entwurfs

| ID | Annahme | Begründung |
|---|---|---|
| EA-1 | argparse statt click/typer | Weniger Deps, explizite Parser-Datei, agentenlesbar |
| EA-2 | unittest oder pytest — eine Wahl in finalem STACK.md; Entwurf ist kompatibel mit beiden | NFA-044 |
| EA-3 | TOML-Write für v1-Schema handgeschrieben / template-basiert | Vermeidet extra Dep; Schema klein |
| EA-4 | `AppContext` frozen dataclass, keine Globals | Testbarkeit |
| EA-5 | Dual TB-Probe: system_profiler primär, ioreg Fallback | R-D01 |
| EA-6 | Topology.complete = Ping **oder** Domain-Match (OP-7) | Analyse A-X / DM-5 |
| EA-7 | Kann-Features (audit, rich) in eigenen Owner-Tags, parallel am Ende | MoSCoW |

---

## 16. Vergleichsvorschau (für Jury, nicht final)

| Kriterium | Tendenz Entwurf A |
|---|---|
| Muss-Anforderungen | Hoch — vollständige Modulabdeckung |
| NFA-Tauglichkeit | Hoch — Timeouts, Plaintext, Offline, Fixtures |
| Einfachheit Runtime | Mittel — viele Dateien, aber flache Logik |
| Testbarkeit | **Sehr hoch** — Kernziel des Entwurfs |
| Betriebsaufwand | Niedrig — ein CLI, User-LaunchAgent, keine Server |
| Agenten-Parallelität | **Sehr hoch** — Dateibesitz-first |

Geeignet als **Siegerkandidat**, wenn die Fabrik-Implementierung primär durch parallele Agenten-Wellen läuft (Standard der Fabrik).

---

## 17. Nächste Schritte (nach Jury)

Falls Entwurf A gewinnt:

1. `ARCHITEKTUR.md` aus diesem Entwurf ausarbeiten (Sequenzdiagramme up/heal, Plist-Felder).
2. `STACK.md` mit exakten Versionen (Python, ruff, optional rich, Test-Runner).
3. ADRs: Subprocess-Runner, LaunchAgent User-Domain, Config-Pfad, Exit-Codes, optional rich, Topology.complete.
4. Planung: `wellen.json` Owner-Tags → Stories mit disjunktem Dateibesitz.
