# Architektur — MacCluster

| Feld | Wert |
|---|---|
| Projekt | MacCluster (`maccluster`) |
| Phase | 2 ARCHITEKTUR |
| Stand | 2026-08-01 |
| Status | **Verbindliche Zielarchitektur** |
| Siegerentwurf | **HYBRID-pragmatisch-agentenfreundlich** |
| Quellen | `10-analyse/*`, `entwuerfe/ENTWURF-{pragmatisch,agentenfreundlich,schnell,skalierbar}.md` |
| Stack | [`STACK.md`](./STACK.md) · ADRs [`adr/`](./adr/) |

Dieses Dokument ist die **maßgebliche Architektur** für Planung und Implementierung.
Code-Bezeichner, Verzeichnisnamen und CLI-Messages sind Englisch; dieses Artefakt ist Deutsch.

---

## 1. Jury — Vergleich der vier Entwürfe

### 1.1 Kriterien und Gewichtung

| # | Kriterium | Gewicht | Messgröße |
|---|---|---|---|
| K1 | **Erfüllung der Muss-Anforderungen** | 5 | Alle A-Muss (A-001…A-045 Muss) + AD-1…AD-6 tragfähig abbildbar |
| K2 | **NFA-Tauglichkeit** | 4 | Performance, Offline, Security-Baseline, Plaintext, Fixture-CI |
| K3 | **Einfachheit** | 4 | Wenige bewegliche Teile; stdlib-first; kein Server/DB/Framework-Ballast |
| K4 | **Testbarkeit** | 4 | Pure Core, mockbare Ports, CI ohne Live-4-Node-HW (NFA-048) |
| K5 | **Betriebsaufwand** | 3 | Ein Package, User-LaunchAgent, keine Cloud, symmetrisch |
| K6 | **Agenten-Parallelität** | 4 | Dateischarfe Module, disjunkter Besitz, klare Importregeln |

Skala je Zelle: **1** (schwach) … **5** (stark). Gewichteter Score = Σ (Punkte × Gewicht). Max = 120.

### 1.2 Vergleichstabelle

| Kriterium (Gewicht) | pragmatisch | agentenfreundlich | schnell | skalierbar |
|---|---|---|---|---|
| **K1 Muss** (×5) | **5** — Alle A-IDs im Monolith-Schnitt; Ensure-Pfad, AD-3/6, Mapping fail-closed | **5** — 1:1-Mapping A-ID → Owner-Tag; vollständige Modulabdeckung | **5** — Muss tragfähig; shared Ensure; Abkürzungen dokumentiert | **5** — Hexagonal Ports decken alle Use-Cases |
| Begründung K1 | Explizite Command→Module-Matrix und Exit-Semantik | Owner-Tags + Checkliste pro A-Gruppe | Ensure teilt up/heal; Privilege-Abkürzung bleibt best-effort | Port-Karte + Use-Case-Schritte je Command |
| **K2 NFA** (×4) | **5** — Timeouts, argv-Runner, Plaintext, Offline, Lock | **5** — ProcessRunner-Allowlist, Timeouts, NO_COLOR, Fixtures | **4** — NFA ok; Dual-TB-Source und Root-Helper bewusst abgekürzt | **5** — Ports + Timeouts + SCA-minimal; Doctor-Registry |
| Begründung K2 | NFA-Matrix im Entwurf abgehakt | Security- und Fixture-First explizit | K11 (ioreg later) schwächt R-D01 leicht | Sehr stark auf Test/NFA, etwas Over-Abstract |
| **K3 Einfachheit** (×4) | **5** — Boring tech; flache `core/`+`ports/`; kein Typer/Pydantic | **3** — Viele Dateien (commands+services+ports+adapters); Boilerplate | **4** — Kompakt; **Typer** als Extra-Dep gegen Brief-Minimalismus | **2** — Registry, viele Ports, hexagonal Overhead für 2–4 Nodes |
| Begründung K3 | „CLI + Datei + LaunchAgent“ | Agentenfreundlich ≠ menschenminimal | Schnell, aber Typer+Click-Transitiv | Wachstumspfad über v1-Bedarf |
| **K4 Testbarkeit** (×4) | **4** — Ports + Fixtures; etwas weniger DI-Formalismus | **5** — AppContext + pure modules + Fake-Ports; Fixture-First | **4** — `osutil.run` mockbar; Typer CliRunner | **5** — Fake-Ports, Domain pure, Contract-Tests |
| Begründung K4 | Gut, aber weniger Owner-Disziplin als A | Kernziel des Entwurfs | Gut genug für MVP | Sehr hoch, teuer in Welle 1 |
| **K5 Betrieb** (×3) | **5** — Ein Binary, User-Agent, keine Server | **5** — Identisch runtime-simpel | **5** — Identisch; Privilege-Lücke klar benannt | **4** — Runtime simpel; mehr interne Komplexität = Wartung |
| Begründung K5 | Minimaler Ops-Fußabdruck | Kein Deployables-Split | K1 Privilege-Rest | Composition Root pflegebedürftig |
| **K6 Agenten** (×4) | **3** — Wellen-Vorschlag; Module gröber → mehr Merge-Risiko | **5** — Owner-Tags, Parallelitätsregeln, Importgrenzen | **3** — dateischarf möglich, aber flacher Schnitt | **4** — Module disjunkt; Registry-Zentrale = Hotspot |
| Begründung K6 | Gut schneidbar, weniger strikt | Maximal parallelisierbar | Weniger Owner-Granularität | Gut, bootstrap.py Konfliktpunkt |
| **Gewichteter Score** | **108** | **112** | **100** | **101** |

### 1.3 Jury-Urteil

| Rang | Entwurf | Score | Kurzfazit |
|---|---|---|---|
| 1 (Basis) | **agentenfreundlich** | 112 | Beste Parallelität und Testbarkeit; leicht übermodularisiert |
| 2 (Basis) | **pragmatisch** | 108 | Beste Einfachheit und boring stack; etwas gröbere Module |
| 3 | skalierbar | 101 | Ports stark, Overhead für v1 zu hoch |
| 4 | schnell | 100 | Time-to-MVP gut; Typer und TB-Abkürzungen schwächen |

**Entscheidung der Geschäftsführung / Chefarchitektur:**

> **HYBRID-pragmatisch-agentenfreundlich** — Runtime und Stack aus **pragmatisch** (argparse, stdlib-first, kein Typer, ein Package, keine DB/Server); Modul- und Agenten-Disziplin aus **agentenfreundlich** (Ports/Adapters, pure Core, AppContext light, Owner-Tags, Importregeln). Beste Ideen aus **schnell** (gemeinsamer Ensure-Pfad up/heal) und **skalierbar** (Network read/apply split, Topology.complete-Regel, ProcessRunner-Allowlist) werden übernommen, **ohne** Plugin-Registry-Framework und **ohne** überfeine services/-Verdoppelung wo unnötig.

| Hybrid-Regel | Quelle | Übernahme |
|---|---|---|
| argparse, keine Typer/Click | pragmatisch + GF | **ja** → ADR-0001 |
| Config-Pfad AD-6, TOML, tomllib | alle / AD-6 | **ja** → ADR-0002 |
| Dual TB: system_profiler + ioreg Fallback | agentenfreundlich / skalierbar | **ja** → ADR-0003 |
| Network read/apply getrennt; allowlist; nur lokal | skalierbar + A-041 | **ja** → ADR-0004 |
| LaunchAgent User-Domain, kein Root-Helper v1 | alle / AD-4 | **ja** → ADR-0005 |
| Topology.complete = Ping ∨ Domain/Link-Match | Analyse OP-7 | **ja** → ADR-0006 |
| Exit 0/1/2/3 | AD-3 | **ja** → ADR-0007 |
| Shared Ensure-Plan für up/heal | schnell | **ja** (`heal_logic/plan.py`) |
| AppContext + Fake-Ports | agentenfreundlich | **ja**, schlank |
| commands dünn; Orchestrierung in services **oder** command+core | agentenfreundlich vereinfacht | **services/** nur wo Logik > ~80 LOC; sonst command → core |
| Kein dynamisches Plugin-System | pragmatisch | **ja** — statische Dispatch-Tabelle in `cli/parser.py` |
| Optional rich extra | Brief Kann | **ja** |

---

## 2. Zielbild (eine Seite)

**MacCluster** ist ein **symmetrisches CLI-Monolith-Package** (`maccluster`), identisch auf jedem von 2–4 Apple-Silicon-Mac-minis. Jeder Member:

1. liest dieselbe logische `cluster.toml` (Soll-Wahrheit),
2. erkennt lokal den Self-Node (Hostname und/oder HW-UUID),
3. mutiert **nur den lokalen Host** (Bridge + feste TB-IP),
4. beobachtet Peers per Ping (optional SSH) und TB-Hardware-Probes.

Kein Leader, kein zentraler Store, kein HTTP-Server, keine Datenbank.
Hintergrundbetrieb = User-Domain-LaunchAgent → `maccluster heal --loop`.

```
┌──────────────────────────────────────────────────────────────────┐
│  maccluster (one process per invocation / LaunchAgent loop)      │
│  cli/argparse  →  commands/*  →  core|services (pure/orch)       │
│         │                    │                                   │
│         │                    ▼                                   │
│         │              domain models + pure logic                │
│         │              (config, mapping, heal plan, topo, health)│
│         │                    │                                   │
│         │         ports/ (Protocols)                             │
│         │                    │                                   │
│         └──── adapters/ (ONLY subprocess + FS mutation) ─────────┘
│                    │                                             │
│     cluster.toml   │   macOS: system_profiler, ioreg, ifconfig,  │
│     mutate.lock    │   networksetup, ping, launchctl, (ssh/iperf)│
│     LaunchAgent    │                                             │
└──────────────────────────────────────────────────────────────────┘
```

**Datenfluss `status`:** resolve config → load/validate → self-match → TB/net/ping probes → HealthSnapshot → plain|json → Exit 0|3.

**Datenfluss `up`/`heal`:** platform guard → validate → lock → map iface (fail-closed) → pure heal plan → network apply (local only) → Exit 0|1|2|3.

---

## 3. Verbindliche Architekturentscheidungen (Kurz)

| ID | Thema | Entscheidung | ADR |
|---|---|---|---|
| D-1 | CLI-Framework | **argparse** (stdlib); kein Typer/Click | [ADR-0001](./adr/ADR-0001-cli-framework-argparse.md) |
| D-2 | Config | TOML; Default `~/.config/maccluster/cluster.toml`; CLI > Env > Default | [ADR-0002](./adr/ADR-0002-config-path-and-format.md) |
| D-3 | TB-Probing | system_profiler primär, ioreg Fallback; pure Parser + Fixtures | [ADR-0003](./adr/ADR-0003-thunderbolt-probing.md) |
| D-4 | Netz-Mutation | argv-only; allowlisted ifaces; nur Self-Host; File-Lock; oft Admin | [ADR-0004](./adr/ADR-0004-network-mutation-and-privileges.md) |
| D-5 | LaunchAgent | User-Domain `gui/$(id -u)`; KeepAlive; kein Root-Helper v1 | [ADR-0005](./adr/ADR-0005-launchagent-user-domain.md) |
| D-6 | Topology.complete | Peer: Ping-erreichbar **oder** Domain/Link-Match | [ADR-0006](./adr/ADR-0006-topology-complete.md) |
| D-7 | Exit-Codes | 0 ok · 1 error · 2 usage · 3 degraded | [ADR-0007](./adr/ADR-0007-exit-codes.md) |
| D-8 | Stack | Python 3.11+; runtime deps leer; optional `rich`; pytest+ruff dev | [`STACK.md`](./STACK.md) |
| D-9 | Subnetz-Default | `10.42.0.0/24` (AD-1); doctor warnt Route-Overlap | Analyse |
| D-10 | SSH | Optional, Default aus (AD-2); BatchMode, Timeout 3 s | Analyse |
| D-11 | schema_version | Config und JSON-Output: Pflichtfeld ≥ 1 | A-042, A-033 |

---

## 4. Schichten und Importregeln

### 4.1 Schichten

| Schicht | Package | Darf I/O? | Verantwortung |
|---|---|---|---|
| **cli** | `maccluster/cli/` | stdout/stderr | argparse, globale Flags, Exit-Mapping, `CliError` |
| **commands** | `maccluster/commands/` | nein (nur über Context) | Dünne Handler: Args → Service/Core → Render → Exit |
| **services** | `maccluster/services/` | über Ports | Orchestrierung komplexer Use-Cases (up, heal, status, doctor, service) |
| **core / domain** | `maccluster/domain/`, pure Untermodule | **nein** | Models, Validierung, Mapping, Heal-Plan, Topo, Health, Doctor-Aggregate |
| **ports** | `maccluster/ports/` | nein | `typing.Protocol` only |
| **adapters** | `maccluster/adapters/` | **ja** | Einzige Stelle mit `subprocess` und OS-FS-Mutation |
| **render** | `maccluster/render/` | stdout only | Plaintext, JSON, optional rich |
| **platform** | `maccluster/platform/` | lesen via Ports | macOS AS Guard, Identity-Orchestrierung |

### 4.2 Harte Importregeln

1. `domain/*`, pure `config` parse/validate, `mapping`, `topology`, `health`, `heal_logic`, `doctor_logic` → **keine** Imports aus `adapters`, `cli`, `commands`.
2. `adapters/*` → darf `domain` und `ports` nutzen; **kein** Import aus `commands`/`render`.
3. `subprocess` **nur** in `adapters/process.py` (ProcessRunner).
4. `shell=True` ist **verboten** (A-044 / NFA-022).
5. Keine globalen Singletons; Produktion verdrahtet `AppContext` in `app_factory.py`.
6. Zyklen verboten. Review-Checkliste genügt v1 (kein import-linter-Pflicht).

### 4.3 AppContext (Kleber)

```python
# Conceptual — English identifiers in product code
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
    audit: AuditPort
```

- Produktion: `AppContext.production()` in `app_factory.py`.
- Tests: Fakes injizieren; kein flächiges Monkeypatch von `subprocess`.

---

## 5. Komponenten und CLI-Mapping

### 5.1 CLI-Unterbefehle (A-X3)

| Befehl | Mutation | Root? | Primäre Module | Exit (AD-3) |
|---|---|---|---|---|
| `tb` | nein | nein | adapters tb, mapping, render | 0 / 1 |
| `init` | Config-Datei | nein | config dump/template, identity | 0 / 2 |
| `config show\|validate` | nein | nein | config load/validate | 0 / 2 |
| `up` | ja | oft ja | heal plan, net_apply, lock, platform | 0 / 1 / 2 / 3 |
| `heal` [`--loop`] | ja | oft ja | wie up + loop | 0 / 1 / 2 / 3 |
| `status` | nein | nein | health, ping, tb | 0 / 1 / 2 / 3 |
| `monitor` | nein | nein | health loop + render | 0 (Ctrl+C) / 1 |
| `topo` | nein | nein | topology, tb | 0 / 1 / 2 |
| `doctor` | nein | nein | doctor_logic + probes | 0 / 1 / 2 / 3 |
| `bench` | nein* | nein | iperf adapter | 0 / 1 / 2 |
| `service install\|uninstall\|status` | ja (plist) | i. d. R. nein | launchagent | 0 / 1 / 2 |

Globale Flags: `--config PATH`, `--json`, `-v`/`--verbose`; Env `NO_COLOR`, `MACCLUSTER_CONFIG`.

### 5.2 Domänenabbildung

| Entität (Analyse) | Code | Persistenz |
|---|---|---|
| ClusterConfig | `domain.models.ClusterConfig` | TOML |
| Node | `domain.models.Node` | in Config; `role` runtime |
| ThunderboltPort / Link | models + tb adapters | Live |
| BridgeInterface | models + network adapters | Live |
| Topology | `topology/*` | abgeleitet |
| HealthSnapshot | `health/*` | flüchtig |
| ServiceState | launchagent adapter | LaunchAgent |
| HealAction | `heal_logic/*` | optional Audit |
| DoctorFinding | `doctor_logic/*` | Ausgabe |
| BenchResult | iperf adapter | optional |

### 5.3 Config-Schema v1 (logisch)

```toml
schema_version = 1
name = "studio-cluster"
subnet = "10.42.0.0/24"
bridge_interface = "bridge0"
heal_interval_seconds = 30
ssh_probes_enabled = false

[[nodes]]
id = "node-a"
hostnames = ["mac-mini-a.local", "mac-mini-a"]
ip = "10.42.0.1"
hw_uuid = "00000000-0000-0000-0000-000000000001"

# node-b … node-d (2–4 total)
```

Validierung (pure): 2–4 Nodes; unique id/ip/hw_uuid; IP ∈ subnet; schema_version supported; interface charset allowlist; self-match genau 1.

### 5.4 Persistenzpfade

| Artefakt | Default-Pfad | Schreibende Komponente |
|---|---|---|
| ClusterConfig | `~/.config/maccluster/cluster.toml` | init / Operator |
| Writer-Lock | `~/.config/maccluster/mutate.lock` | up / heal / service install |
| LaunchAgent Plist | `~/Library/LaunchAgents/com.maccluster.heal.plist` | service install |
| Action-Log (opt-in) | `~/.local/state/maccluster/actions.log` | audit (Kann) |

Pfad-Override Config: `--config` > `MACCLUSTER_CONFIG` > Default (AD-6 / A-040).

### 5.5 Ensure-Schritte (up / heal, lokal only)

1. Platform guard (mutate) → Exit 2 if unsupported.
2. Load + validate config → Exit 2.
3. Acquire file lock (A-031).
4. Resolve target interface: config override > mapping > **fail closed** Exit 2 (A-039).
5. Pure plan: desired vs observed → `HealAction[]` (noop if already configured).
6. Apply allowlisted: ensure bridge, admin-up, Self-IP (no Wi-Fi/default-route).
7. If bridge/IP ok but no TB link → Exit **3** (A-011); hard fail privilege → Exit **1**.
8. Release lock; optional audit.

---

## 6. Verzeichnisbaum des Produkts

Pfade relativ zu `projects/maccluster/` (Produkt-Git-Root). Englische Bezeichner.

```text
maccluster/
├── LICENSE                          # MIT (W1 G1)
├── README.md                        # EN: install, commands, exit codes, config, mapping, best-effort
├── CHANGELOG.md
├── pyproject.toml                   # package, scripts, optional [monitor] rich
├── requirements-dev.txt             # pytest, ruff, pip-audit
├── uv.lock                          # or requirements.lock (G2)
├── Makefile                         # verify = ruff + pytest
├── install.sh                       # pipx/pip convenience
├── .gitignore
├── .github/
│   ├── workflows/ci.yml             # ruff + pytest
│   └── dependabot.yml
├── examples/
│   └── cluster.toml                 # 4-node placeholders 10.42.0.1–.4
├── docs/
│   ├── receptacle-mapping.md        # Mini layouts + override (A-039)
│   └── faq/                         # USER/ADMIN/AUTHOR/DEVELOPER (Abnahme)
├── src/maccluster/
│   ├── __init__.py                  # __version__
│   ├── __main__.py
│   ├── app_factory.py               # AppContext.production()
│   ├── constants.py                 # defaults: subnet, intervals, paths, labels
│   ├── errors.py                    # CliError(exit_code, message, details)
│   │
│   ├── cli/
│   │   ├── main.py                  # entry + dispatch
│   │   ├── parser.py                # argparse tree
│   │   └── exit_codes.py            # 0/1/2/3
│   │
│   ├── commands/                    # thin handlers
│   │   ├── tb.py
│   │   ├── init_cmd.py
│   │   ├── config_cmd.py
│   │   ├── up.py
│   │   ├── heal.py
│   │   ├── status.py
│   │   ├── monitor.py
│   │   ├── topo.py
│   │   ├── doctor.py
│   │   ├── bench.py
│   │   └── service_cmd.py
│   │
│   ├── services/                    # orchestration (use-cases)
│   │   ├── init_service.py
│   │   ├── config_service.py
│   │   ├── tb_service.py
│   │   ├── mutate_service.py        # shared up + heal one-shot
│   │   ├── heal_loop_service.py
│   │   ├── status_service.py
│   │   ├── monitor_service.py
│   │   ├── topo_service.py
│   │   ├── doctor_service.py
│   │   ├── bench_service.py
│   │   └── service_mgmt.py
│   │
│   ├── domain/
│   │   ├── models.py                # ClusterConfig, Node, Ports, Snapshot, …
│   │   ├── enums.py
│   │   └── invariants.py
│   │
│   ├── config/
│   │   ├── paths.py                 # AD-6 resolution
│   │   ├── schema.py                # schema_version constants
│   │   ├── load.py                  # TOML text → models (pure given text)
│   │   ├── validate.py
│   │   ├── dump.py                  # models → TOML text (template/handwritten)
│   │   └── init_template.py
│   │
│   ├── platform/
│   │   ├── guard.py                 # A-043
│   │   └── identity.py              # self-match orchestration
│   │
│   ├── mapping/
│   │   ├── receptacle.py            # pure map + ambiguity fail-closed
│   │   └── layouts.py               # known mini layout tables
│   │
│   ├── topology/
│   │   ├── match.py
│   │   └── build.py
│   │
│   ├── health/
│   │   ├── snapshot.py
│   │   └── aggregate.py             # overall + exit hint
│   │
│   ├── heal_logic/
│   │   ├── plan.py                  # observed vs desired → HealAction[]
│   │   └── idempotency.py
│   │
│   ├── doctor_logic/
│   │   ├── checks.py
│   │   └── report.py                # severity → exit (A-X2)
│   │
│   ├── ports/                       # Protocol only
│   │   ├── clock.py
│   │   ├── filesystem.py
│   │   ├── process.py
│   │   ├── thunderbolt.py
│   │   ├── network.py               # NetworkReadPort + NetworkApplyPort
│   │   ├── reachability.py
│   │   ├── service.py
│   │   ├── bench.py
│   │   ├── lock.py
│   │   ├── identity.py
│   │   ├── platform.py
│   │   └── audit.py
│   │
│   ├── adapters/
│   │   ├── process.py               # ONLY subprocess entry (timeouts, no shell)
│   │   ├── filesystem.py            # atomic write, 0600, backup
│   │   ├── clock.py
│   │   ├── identity_macos.py
│   │   ├── platform_macos.py
│   │   ├── tb_system_profiler.py
│   │   ├── tb_ioreg.py
│   │   ├── network_read.py
│   │   ├── network_apply.py         # allowlist; dry_run param
│   │   ├── ping_macos.py
│   │   ├── ssh_probe.py             # optional BatchMode
│   │   ├── lock_file.py             # PID + stale takeover
│   │   ├── launchagent.py
│   │   ├── plist_template.py
│   │   └── iperf3.py
│   │
│   ├── render/
│   │   ├── symbols.py               # UP/DOWN text symbols (no color-only)
│   │   ├── plain.py
│   │   ├── json_out.py              # schema_version envelopes
│   │   ├── sanitize.py
│   │   └── rich_monitor.py          # optional; importlib guard
│   │
│   └── audit/
│       └── log.py                   # optional rotate 5 MiB
│
└── tests/
    ├── conftest.py
    ├── fixtures/
    │   ├── system_profiler/
    │   ├── ioreg/
    │   ├── ifconfig/
    │   ├── configs/                 # valid 2/4, invalid 1/5, dup ip, no schema
    │   └── topology/
    ├── unit/
    │   ├── domain/
    │   ├── config/
    │   ├── mapping/
    │   ├── topology/
    │   ├── health/
    │   ├── heal_logic/
    │   ├── doctor_logic/
    │   ├── platform/
    │   ├── render/
    │   ├── adapters/
    │   ├── cli/
    │   └── services/
    └── integration/
        ├── test_cli_help.py
        ├── test_init_roundtrip.py
        ├── test_status_json_schema.py
        ├── test_doctor_exit.py
        └── test_mutate_guard.py
```

---

## 7. Modulgrenzen und Dateibesitz (Owner-Tags)

### 7.1 Owner-Tag → Dateien → A-IDs

| Owner-Tag | Primäre Dateien | A-IDs / Stories |
|---|---|---|
| **shared-w1** | `pyproject.toml`, `LICENSE`, CI, `Makefile`, `cli/*` skeleton, `__main__`, `constants`, `errors`, `app_factory` stub | Gerüst G1–G5 |
| **domain-models** | `domain/*` | Domänenabbildung |
| **config-core** | `config/paths|schema|load|validate|dump.py` | A-005–A-007, A-027, A-040, A-042 |
| **config-init** | `config/init_template.py`, `commands/init_cmd.py`, `services/init_service.py`, `examples/cluster.toml` | A-003, A-004, A-008 |
| **platform** | `platform/*`, `adapters/platform_macos.py`, `adapters/identity_macos.py` | A-007, A-043 |
| **adp-process** | `adapters/process.py`, `ports/process.py` | A-044, A-045 |
| **mapping** | `mapping/*`, `docs/receptacle-mapping.md` | A-039 |
| **adp-tb** | `adapters/tb_*.py`, `ports/thunderbolt.py` | A-001, A-002 |
| **cmd-tb** | `commands/tb.py`, `services/tb_service.py` | A-001 |
| **adp-net** | `adapters/network_*.py`, `ports/network.py` | A-009–A-012, A-041 |
| **adp-lock** | `adapters/lock_file.py`, `ports/lock.py` | A-031 |
| **heal-logic** | `heal_logic/*` | A-009–A-013, A-038 |
| **svc-mutate** | `services/mutate_service.py`, `commands/up.py`, `commands/heal.py` | A-009–A-014, A-038, A-041 |
| **svc-heal-loop** | `services/heal_loop_service.py` | A-014 |
| **adp-ping** | `adapters/ping_macos.py`, `ports/reachability.py` | A-018, A-045 |
| **health** | `health/*` | A-018–A-020 |
| **svc-status** | `services/status_service.py`, `commands/status.py` | A-018, A-019 |
| **svc-monitor** | `services/monitor_service.py`, `commands/monitor.py` | A-020, A-021 |
| **topology** | `topology/*`, `commands/topo.py`, `services/topo_service.py` | A-022, A-023 |
| **doctor-logic** | `doctor_logic/*`, `services/doctor_service.py`, `commands/doctor.py` | A-024, A-026 |
| **adp-launchd** | `adapters/launchagent.py`, `adapters/plist_template.py`, `services/service_mgmt.py`, `commands/service_cmd.py` | A-015–A-017 |
| **adp-ssh** | `adapters/ssh_probe.py` | A-032 |
| **adp-iperf** | `adapters/iperf3.py`, `commands/bench.py`, `services/bench_service.py` | A-025, A-026 |
| **render-plain** | `render/plain.py`, `render/symbols.py`, `render/sanitize.py` | A-021, NFA-032 |
| **render-json** | `render/json_out.py` | A-033 |
| **render-rich** | `render/rich_monitor.py` | A-037 Kann |
| **audit** | `audit/log.py` | A-036 Kann |
| **adp-fs** | `adapters/filesystem.py`, `ports/filesystem.py` | Config write 0600, backup |

### 7.2 Parallelitätsregeln für Implementierungs-Agenten

1. **Ein Agent = ein Owner-Tag** pro Task; Diff nur Dateien dieses Tags + zugehörige Tests.
2. **Ports zuerst** stabilisieren (Welle 1/2); Adapter und Services hängen daran.
3. **Keine Cross-Edits** an `domain/models.py` ohne Story `domain-models`.
4. **Fixture-First** für Parser: Fixture + Test mit/vor Parser-Code.
5. Mutationen immer: `platform.guard → validate → lock → plan → apply`.
6. CLI-Messages Englisch; Exit-Codes nur aus `cli/exit_codes.py`.

### 7.3 Wellen-Schnitt (Vorschlag für Planung)

| Welle | Owner-Tags (disjunkt) | Ergebnis |
|---|---|---|
| **W1** | shared-w1, domain-models (stubs), adp-process, cli exit/parser skeleton | Package installierbar, `--help`, verify grün |
| **W2** | config-core, config-init, platform, adp-fs | init, config show/validate, self-match, platform guard |
| **W3** | mapping, adp-tb, cmd-tb, render-plain (basis) | `tb`, mapping fixtures, docs mapping |
| **W4** | adp-net, adp-lock, heal-logic, svc-mutate | `up`, `heal` one-shot, lock, Exit 3 no link |
| **W5** | adp-ping, health, svc-status, svc-monitor, topology, render-json | status, monitor, topo, `--json` |
| **W6** | doctor-logic, adp-launchd, svc-heal-loop, adp-ssh, adp-iperf, render-rich, audit | doctor, service, loop, bench, Kann-Features |

Abhängigkeiten nur auf **frühere** Wellen. Planung finalisiert Stories und exakten Dateibesitz in `wellen.json`.

---

## 8. Fehler-, Privilegien- und Konfigurationsstrategie

### 8.1 Exit-Codes (verbindlich AD-3)

| Code | Name | Verwendung |
|---|---|---|
| 0 | OK | Erfolg; Monitor Ctrl+C; doctor nur info-warns (z. B. fehlendes iperf3) |
| 1 | ERROR | Runtime, Rechte, OS-Fail, Probe-Crash, `bench` ohne iperf3 |
| 2 | USAGE | Args, Config, Validation, unsupported mutate platform, Mapping-Ambiguität |
| 3 | DEGRADED | Peer down (status); up ohne TB-Link aber IP gesetzt; doctor warn-cluster |

- `CliError(exit_code, message, details)` — Services werfen, `main` mappt; kein Traceback im Normalfall; `-v` darf Stack zeigen.
- status: alle Peers up → 0; ≥1 Peer down, Self ok → 3 (A-X1).
- doctor: worst `error` → 1; warn reachability/bridge ohne error → 3; nur optionale Info-Warns → 0 (A-X2).

### 8.2 Privilegien

| Befehl | Root? |
|---|---|
| tb, status, monitor, topo, lesender doctor, config show/validate | nein |
| init (Home-Config) | nein |
| up, heal (Korrektur) | oft ja — Meldung `admin/sudo required`, Exit 1 |
| service install/uninstall | User-Domain; i. d. R. ohne root für Plist |

**Kein setuid-/Root-Helper in v1** (ADR-0005). LaunchAgent nach Login; wenn Bridge root braucht: Operator `sudo maccluster up|heal` interaktiv; Agent meldet fehlende Rechte statt silent success.

### 8.3 Timeouts (A-045)

| Probe | Default |
|---|---|
| ping / peer | ≤ 2 s (status Gesamtbudget < 3 s) |
| SSH | 3 s, BatchMode |
| iperf3 | ≤ 5 s test + hard cap |
| generischer OS-Befehl | ~15 s |

### 8.4 Secrets und Security

**Maßgeblich für Implementierung und Code-Review.** Details und Befunde: [`SICHERHEIT.md`](./SICHERHEIT.md). Ergänzt ADR-0002 / ADR-0004.

#### 8.4.1 Grundsätze

- **AuthN:** ausschließlich lokaler macOS-Benutzer (+ optional interaktives `sudo` durch den Operator). Kein App-Login, OAuth, Token, Session (NFA-018).
- **AuthZ:** eine Rolle Operator; Read-only ohne Root; Mutationen least-privilege und **nur lokal** (A-041). Kein Remote-Write, kein Root-Helper v1.
- **Secrets:** keine Passwörter/Tokens/Private-Keys in Repo, Beispielen, Config, Audit-Log, JSON oder Verbose-Stdout (NFA-020, A-044). SSH nutzt OS-Agent/Keys des Operators; Config darf höchstens Key-**Pfade** enthalten, nie Key-Material.
- **PII:** nur technische Betriebsdaten (Hostname, HW-UUID, IP, Link-State, Timestamps) — NFA-028. Keine Telemetrie (NFA-029).
- **Klartext-Config:** bewusst (NFA-026); Schutz = Dateirechte + keine Secrets im Inhalt.

#### 8.4.2 ProcessRunner (einzige Subprocess-Schicht)

| Regel | Vorgabe |
|---|---|
| Ort | **Nur** `adapters/process.py` — Importregel §4.2 |
| Shell | `shell=False` **immer**; keine String-Interpolation von Config/CLI in Shell |
| argv | `list[str]` only; Timeouts Pflicht (A-045 / §8.3) |
| Allowlist (Basename) | `system_profiler`, `ioreg`, `ifconfig`, `networksetup`, `ping`, `launchctl`, `sw_vers`, `sysctl`, `scutil`, `iperf3`, `ssh` |
| Pfad-Auflösung | Basename → **absoluter** Pfad nur aus Suchpfad `/usr/sbin`, `/sbin`, `/usr/bin`, `/bin`; für `iperf3` zusätzlich `/opt/homebrew/bin`, `/usr/local/bin`. Nicht gefunden → klarer Fehler, kein freier PATH-Lookup unter Privilege |
| ENV an Kinder | Minimal: `PATH=/usr/bin:/bin:/usr/sbin:/sbin`, `HOME`, `USER`, `LANG`/`LC_ALL` (C wo Parser-stabil). **Keine** Weitergabe von `DYLD_*` oder unfiltered Parent-ENV (RF-X-09) |
| Tests | Sonderzeichen in Hostname/Pfad; Non-Allowlist-Binary abgelehnt (A-044) |

#### 8.4.3 Dateisystem: Config, Lock, Rechte

| Regel | Vorgabe |
|---|---|
| Neue Config | Mode **`0600`** (NFA-027) |
| Atomic write | Temp-Datei im **selben Verzeichnis** + `os.replace` |
| **Symlink-Policy** | Vor Create/Overwrite/Lock: `lstat` — Ziel darf **kein Symlink** sein; sonst Exit **2**, keine Schreiboperation (RF-X-11 / RF-A21). Gilt für Config-Pfad und `mutate.lock` |
| `init` overwrite | Ohne `--force` Exit 2; mit `--force` Backup dann Replace nur wenn Policy ok (A-004) |
| Config-Größe | Ablehnen ab **1 MiB** (RF-F2-18) |
| Audit-Log | Default **aus**; bei Opt-in Rotation max 5 MiB; Inhalt: Timestamp, Aktion, Ergebnis, iface/IP — **keine** Secrets (NFA-024) |

#### 8.4.4 Eingabevalidierung (Config / CLI)

Pure Validierung vor jedem Mutate (fail-closed):

| Feld | Regel |
|---|---|
| `schema_version` | int ≥ 1, supported set |
| Nodes | genau 2–4; unique `id`, `ip`, `hw_uuid` |
| `ip` / `subnet` | `ipaddress`-Parse; IP ∈ Subnet |
| Interface-Name (`bridge_interface`, Override) | `^[A-Za-z][A-Za-z0-9_.-]{0,15}$` — sonst Exit 2 (RF-F3-15) |
| Node `id` | `^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$` |
| Hostnames | druckbare Labels; Steuerzeichen abgelehnt; Anzeige zusätzlich sanitizen (§8.4.6) |
| CLI `--config` / Env-Pfad | aufgelöst; bei Write-Ops Symlink-Policy |

#### 8.4.5 NetworkApply — Allowlist und Verbote

- **Nur** Self-Host; **nur** Interfaces aus Config-Override oder Mapping-Ergebnis (nach §8.4.4 validiert).
- **Erlaubt:** ensure bridge present; interface admin-up; Self-IP setzen/bestätigen (idempotent).
- **Verboten:** Default-Route ändern; globale DNS; Wi-Fi Power; IPs auf fremden Ifaces (z. B. `en0`); SSH/remote ifconfig; `shell=True`; unvalidierte Interface-Strings aus Config.
- Mapping-Ambiguität → Exit **2**, kein Raten (A-039).
- Preflight Rechte: fehlend → Exit **1**, Message enthält `admin/sudo required`; **kein** silent Exit 0 (A-012).
- `dry_run: bool` an Apply-Funktionen für Tests.

#### 8.4.6 Ausgabe-Sanitizing

- `render/sanitize.py`: Control/ANSI-Sequenzen aus Hostnames und sonstigen untrusted Strings vor Terminal-Ausgabe entfernen/escapen (RF-F5-20).
- Kritische Zustände nie nur farblich (NFA-032); `NO_COLOR` respektieren.

#### 8.4.7 SSH (optional, Default aus)

- Nur wenn Config-Flag **und** Keys vorhanden (AD-2 / A-032).
- argv-Flags mindestens: `BatchMode=yes`, `ConnectTimeout=3`, `PasswordAuthentication=no`, `KbdInteractiveAuthentication=no`.
- Host-Key-Mismatch: Fehler/Warnung, **kein** Default `StrictHostKeyChecking=no`; Fallback auf lokale Probes.
- Kein Password-Prompt-Hang; Timeout greift (NFA-023).

#### 8.4.8 Bench / LaunchAgent

- **Bench:** Ziel nur validierte IP (Config-Node oder strikte CLI-IP-Parse); `iperf3` abs. Pfad; Duration-Cap (Default ≤ 5 s, hart max 60 s) — RF-F7-11/12.
- **LaunchAgent:** `ProgramArguments[0]` = absoluter Pfad zu `maccluster`; Args ohne Shell; `--config` mit aufgelöstem Pfad; Label `com.maccluster.heal`; ThrottleInterval ≥ 10 s (ADR-0005).

#### 8.4.9 Abhängigkeiten / SCA

- Runtime: **keine** Pflicht-Deps; optional `rich` MIT, pin `>=13.7,<15` (STACK).
- Abnahme: `pip-audit` ohne offene critical/high; Dependabot; keine GPL/AGPL-Runtime (NFA-025, QUALITAET §4.5–4.7).

### 8.5 Konfiguration / Env

| Quelle | Inhalt |
|---|---|
| `cluster.toml` | Cluster-Soll (keine Secrets) |
| `--config` / `MACCLUSTER_CONFIG` | Pfad-Override |
| `NO_COLOR` | keine ANSI-Farben |
| `MACCLUSTER_RICH=0` | rich erzwingen aus (optional) |

---

## 9. Teststrategie

| Ebene | Was | HW? |
|---|---|---|
| Unit pure | validate, self-match, mapping, topo, heal plan, health/doctor exit | nein |
| Unit adapter | Parser gegen Fixtures; argv safety; lock stale | nein |
| Integration | CLI mit Fake-Context / tmp HOME; Exit-Codes; JSON schema | nein |
| Manuell Abnahme | 2–4 Mini, Reboot A-038, LaunchAgent KeepAlive | ja |

**Pflicht-Fixtures (CI):** TB Mini-Sample connected/unconnected; malformed profiler; Config 1/2/4/5 Nodes; dup IP; ambiguous self; partial mesh; mapping ambiguity; Hostname mit Sonderzeichen.

**Verify:** `make verify` → `ruff check` + `ruff format --check` + `pytest`.

**Abdeckung:** Geschäftslogik domain/heal_logic/config ≥ 80 % (QUALITAET). Kein Live-4-Node in CI.

---

## 10. NFA-Abdeckung (Kurz)

| NFA-Gruppe | Architektur-Mechanismus |
|---|---|
| Performance 001–003 | sequenzielle Probes + Timeouts; schlanker Monitor-Loop; Idle-Heal < 5 s |
| Skalierung 008–011 | Validate 2–4; kein Server; single-writer lock |
| Verfügbarkeit 012–016 | heal + LaunchAgent KeepAlive; idempotent ensure; peer-down robust |
| Security 018–027 | OS-Auth; ProcessRunner abs+allowlist; Symlink-Policy; iface regex; no secrets; 0600; SCA — siehe §8.4 / SICHERHEIT.md |
| Datenschutz 028–030 | nur Tech-Felder; local files; no telemetry |
| A11y 032–035 | symbols; NO_COLOR; plain without rich; pipe-Verhalten monitor |
| Plattform 040–043 | platform guard; pipx; one package; Python 3.11+ |
| Observability 045–046 | Exit 0–3; `--json` + schema_version |
| Test 048–049 | ports + fixtures + FakeClock |

---

## 11. Risiken und Zielkonflikte (Gate-relevant)

| ID | Risiko / Konflikt | Schwere | Architektur-Mitigation | Rest für Gate |
|---|---|---|---|---|
| R1 | **User-LaunchAgent ohne Root** kann Bridge nach Reboot nicht setzen | hoch | Klar melden; README sudo-Pfad; doctor-Check; A-038 best-effort | Gate 4: akzeptiert best-effort oder Root-Helper-CR |
| R2 | **Receptacle→Iface-Mapping** falsch / OS-Drift | hoch | Isoliertes mapping + Fixtures + Override + fail-closed mutate | Manuelle Mini-Abnahme |
| R3 | **system_profiler/ioreg Format-Drift** | mittel | Dual-Source + multi-sample Fixtures | Pflege Fixtures bei OS-Updates |
| R4 | **ifconfig Schaden** an Fremd-Interfaces | mittel | §8.4.5 Forbidden-Ops + iface-Regex + abs ProcessRunner | Code-Review Security Welle 4 |
| R5 | Hybrid hat **mehr Dateien** als flacher Monolith | niedrig | Owner-Tags kompensieren; Welle 1 Gerüst strikt | Planer schneidet Wellen disjunkt |
| R6 | **Privilege vs. Symmetrie** (sudo nur interaktiv) | mittel | Dokumentiert; kein silent success | Operator-Runbook |

**Zielkonflikt Einfachheit ↔ Agenten-Parallelität:** bewusst zugunsten **moderater Modularität** (Hybrid) gelöst — mehr Dateien als pragmatisch-flat, weniger als skalierbar-hexagonal-maximal.

---

## 12. ANNAHMEN dieser Architektur

| ID | Annahme | Begründung |
|---|---|---|
| AR-1 | Hybrid pragmatisch+agentenfreundlich ist Sieger | GF-Empfehlung + Jury-Scores |
| AR-2 | argparse statt Typer | ADR-0001; stdlib; NFA-007 |
| AR-3 | Kein Root-Helper v1 | AD-4; OP-5; best-effort A-038 |
| AR-4 | Topology.complete = Ping ∨ Link/Domain-Match | OP-7 / ADR-0006 |
| AR-5 | TOML-Write template/handwritten für Schema v1 | Vermeidet extra Dep |
| AR-6 | pytest + ruff als Dev-Toolchain | NFA-044; Industrie-Default |
| AR-7 | hatchling oder setuptools — final in STACK | Packaging egal solange pipx |
| AR-8 | LaunchAgent Label `com.maccluster.heal` | DNS-reverse üblich |
| AR-9 | min. macOS: getestete Version in README; älter = warn | OP-8 |
| AR-10 | services/ für Orchestrierung, nicht für jede 20-LOC-Command zwingend dünn halten | Vermeidet Over-Split; Agenten-Regeln bleiben |

---

## 13. Traceability Muss-Anforderungen → Architektur

| Gruppe | A-IDs | Ort |
|---|---|---|
| TB-Info | A-001, A-002, A-039 | adp-tb, mapping, cmd-tb |
| Config/init | A-003–A-007, A-040, A-042 | config/*, config-init |
| up/heal | A-009–A-014, A-038, A-041 | heal_logic, adp-net, svc-mutate |
| status/monitor | A-018–A-021 | health, svc-status/monitor, render |
| topo | A-022, A-023 | topology/* |
| doctor | A-024 | doctor_logic |
| Robustheit | A-027–A-030, A-043, A-044 | config, platform, process |
| Install/Offline | A-034, A-035 | packaging, no network clients |
| Soll Vollausbau | A-015–A-017, A-025–A-026, A-031–A-033, A-045 | launchd, iperf, lock, json, timeouts |
| Kann | A-036, A-037 | audit, rich_monitor |

---

## 14. Nächste Phase

1. Planung: `BACKLOG.md` + `wellen.json` mit Owner-Tags aus §7.
2. Implementierung Welle 1: Gerüst nach QUALITAET G1–G5; Security-Baseline §8.4 / [`SICHERHEIT.md`](./SICHERHEIT.md) einhalten.
3. Security-Review nach Welle 4 (mutate-Pfad: `process`, `network_apply`, Symlink/Lock).
4. Gate 4: manuelle 2–4-Node-Abnahme inkl. A-038; SCA `pip-audit` + Secret-Scan.

---

*Ende ARCHITEKTUR.md — verbindlich für Planung und Implementierung.*
