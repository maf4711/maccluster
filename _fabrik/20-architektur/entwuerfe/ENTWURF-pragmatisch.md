# Entwurf: Pragmatisch — MacCluster

| Feld | Wert |
|---|---|
| Projekt | MacCluster (`maccluster`) |
| Entwurf-ID | ENTWURF-pragmatisch |
| Phase | 2 ARCHITEKTUR |
| Stand | 2026-08-01 |
| Leitlinie | **Boring tech** — Monolith-CLI, stdlib-first, minimale Dependencies, kein Server, keine DB |
| Quelle | Brief, `ANFORDERUNGEN.md`, `NFA.md`, `DOMAENENMODELL.md`; Skelett `templates/product-skeletons/CLI.md` |

Dieser Entwurf maximiert **Einfachheit und Betriebsarmut**: ein Python-Paket, ein Prozess pro CLI-Aufruf (bzw. ein LaunchAgent-Prozess im Heal-Loop), Datei-Config, OS-Bordmittel hinter schmalen Ports. Keine Microservices, keine Plugins, keine ORM, kein Netzwerk-Daemon.

---

## 1. Entwurfsidee (eine Seite)

**MacCluster** ist ein **symmetrisches CLI-Monolith** (`maccluster`), identisch auf jedem von 2–4 Apple-Silicon-Mac-minis installiert. Jeder Member:

1. liest dieselbe logische `cluster.toml` (Soll-Wahrheit),
2. erkennt lokal, welcher Node `self` ist (Hostname / HW-UUID),
3. mutiert **nur den lokalen Host** (Bridge + feste TB-IP),
4. beobachtet Peers per Ping (optional SSH) und TB-Hardware-Probes.

Kein Leader, kein zentraler Store, kein HTTP. Persistenz = eine TOML-Datei (+ optional Action-Log). Hintergrundbetrieb = User-Domain-LaunchAgent, der `heal --loop` startet.

```
┌─────────────────────────────────────────────────────────────┐
│  maccluster (single process / invocation)                   │
│  ┌──────────┐  ┌────────────┐  ┌─────────────────────────┐  │
│  │ cli/     │→ │ commands/  │→ │ domain (pure) + ports   │  │
│  │ argparse │  │ tb,init,up │  │ config · map · heal ·   │  │
│  │ exit 0-3 │  │ heal,mon…  │  │ topo · doctor · render  │  │
│  └──────────┘  └────────────┘  └───────────┬─────────────┘  │
│                                            │                 │
│              ┌─────────────────────────────┼─────────────┐  │
│              ▼                             ▼             ▼  │
│     HostOS adapters              file I/O           optional │
│  system_profiler/ioreg          cluster.toml        rich TUI │
│  ifconfig/networksetup          lockfile            iperf3   │
│  ping / launchctl / ssh         action.log          (PATH)   │
└─────────────────────────────────────────────────────────────┘
```

**Datenfluss (typisch `status`):** Config laden → Self matchen → Bridge/Ports/Ping proben → `HealthSnapshot` bauen → Text oder JSON rendern → Exit 0/3.

**Datenfluss (typisch `up`/`heal`):** Config validieren → Platform-Guard → Writer-Lock → Ist vs. Soll → `ifconfig`/`networksetup` (argv-separiert) → Ergebnis melden → Exit 0/1/3.

---

## 2. Stack

### 2.1 Sprachen & Runtime

| Schicht | Wahl | Version / Constraint | Begründung |
|---|---|---|---|
| Sprache | **Python** | **3.11+** (Classifier `>=3.11`) | Brief G bindend; auf macOS AS verfügbar; schnelle Parser/Fixtures |
| Packaging | **pyproject.toml** + setuptools (oder hatchling) | PEP 621 | Standard, `pip`/`pipx` |
| Entry point | Console script `maccluster` | `maccluster.__main__` / `cli:main` | Ein Binary-Name im PATH |
| CLI-Parsing | **`argparse`** (stdlib) | — | Unterkommandos ohne Extra-Dep; boring |
| Config-Format | **TOML** via **`tomllib`** (stdlib 3.11+) | Schema v1 | Lesen stdlib; Schreiben: `tomli-w` **oder** einfache Template-Emission ohne Dep |
| Dataclasses / Typing | stdlib `dataclasses`, `typing`, `enum`, `ipaddress` | — | Validierung ohne Pydantic |
| Subprocess | `subprocess.run(..., shell=False, timeout=…)` | — | NFA-022 / A-044 |
| JSON | stdlib `json` | `schema_version` Feld | A-033 |
| Optional TUI | **`rich`** (extra) | aktuell stabil, MIT | Kann A-037; Kern ohne rich voll nutzbar (NFA-033) |
| Tests | **pytest** | ≥7 | Fixture-Tests; CI |
| Lint/Format | **ruff** | aktuell stabil | Ein Tool für Lint+Format; CI |

**Keine** Dependencies für: HTTP, ORM, Async-Framework, Plugin-System, gRPC, YAML, Click/Typer (vermeidbar), pydantic (vermeidbar).

### 2.2 Datenhaltung

| Artefakt | Ort | Format | Rechte |
|---|---|---|---|
| Cluster-Config (Soll) | Default `~/.config/maccluster/cluster.toml` | TOML, `schema_version = 1` | neu: `0600` (NFA-027) |
| Config-Override | CLI `--config` > Env `MACCLUSTER_CONFIG` > Default (AD-6) | — | — |
| Writer-Lock | `~/.config/maccluster/mutate.lock` (oder neben Config) | File-Lock (fcntl / exclusive open) | pro Host |
| LaunchAgent Plist | `~/Library/LaunchAgents/ai.maccluster.heal.plist` (Label final in Impl.) | XML plist | User-Domain (AD-4) |
| Action-Log (opt-in) | `~/.config/maccluster/actions.log` | Append-Text/JSONL | Rotation max 5 MiB (NFA-010) |
| Beispiel-Config | Repo `examples/cluster.toml` | TOML Platzhalter | im Lieferumfang |

**Keine Datenbank.** Keine Cloud-Persistenz. Operator versioniert Config selbst (Dotfiles).

### 2.3 Hosting / Betrieb

| Aspekt | Entscheidung |
|---|---|
| Deployment | Lokal auf jedem Member: `pipx install .` / `pip install -e .` / `install.sh` |
| Server | **Keiner** — kein Listener, kein Daemon außer LaunchAgent→CLI |
| Cloud | Nein |
| Container | Nein (macOS TB braucht Host-Netz) |
| CI | GitHub Actions: `macos-latest` optional + Linux für pure Unit-Parser (Fixture); Lint + pytest |
| Secrets | Keine im Repo; SSH-Keys nur OS-Agent des Operators |

### 2.4 Lizenzen

| Komponente | Lizenz-Erwartung |
|---|---|
| Produkt MacCluster | **MIT** (Welle-1 `LICENSE`, QUALITAET G1) |
| Python stdlib | PSF |
| `rich` (optional) | MIT |
| `pytest`, `ruff` (dev) | MIT |
| OS-Tools (`system_profiler`, …) | macOS, nicht gebündelt |

Keine GPL/AGPL-Abhängigkeiten. SCA: `pip-audit` in verify-Kette; Dependabot für `pip`.

### 2.5 Externe Tools (nicht Python-Packages)

| Tool | Pflicht | Rolle |
|---|---|---|
| `system_profiler` / `ioreg` | ja (macOS) | TB-Hardware lesen |
| `ifconfig` / `networksetup` | ja | Bridge/IP lesen/schreiben |
| `ping` | ja | Peer-Reachability |
| `launchctl` | ja (service) | LaunchAgent |
| `sw_vers` / `sysctl` / `system_profiler SPHardwareDataType` | ja | Platform/Self-Identity |
| `iperf3` | nein | `bench` only |
| `ssh` | nein | optionale Probes |

---

## 3. Systemschnitt (Module)

Ziel: **dateischarfer Besitz** für Wellen, Ports für OS-I/O, reine Domänenlogik ohne Subprocess.

### 3.1 Schichten

| Schicht | Paketpfad (EN) | Verantwortung | Darf I/O? |
|---|---|---|---|
| CLI | `maccluster/cli.py` | Argparse, globale Flags (`--config`, `--json`, `-v`, `NO_COLOR`), Exit-Mapping | stdout/stderr |
| Commands | `maccluster/commands/*` | Orchestrierung pro Subcommand; ruft Domain + Ports | ja (über Ports) |
| Domain / Core | `maccluster/core/*` | Validierung, Self-Match, Mapping, Heal-Plan, Topo-Match, Doctor-Aggregate, Exit-Semantik | **nein** |
| Models | `maccluster/models.py` | Dataclasses: ClusterConfig, Node, Ports, Snapshot, … | nein |
| Ports (Interfaces) | `maccluster/ports/*` | Abstrakte Protokolle + Default-Adapter | Adapter: ja |
| Render | `maccluster/render/*` | Text / JSON / optional rich | stdout only |
| Service | `maccluster/service/*` | Plist-Templates, launchctl-Wrapper | ja |
| Platform | `maccluster/platform.py` | macOS AS Guard (A-043) | lesen |

### 3.2 Modul-Tabelle (Implementierungsgrenzen)

| Modul | Dateien (Ziel) | Kern-APIs (Skizze) | Deckt |
|---|---|---|---|
| **config** | `core/config_load.py`, `core/config_validate.py`, `ports/config_store.py` | `load_config(path) → ClusterConfig`; `validate(cfg)`; `resolve_config_path(cli, env)` | A-003–A-008, A-027, A-040, A-042 |
| **identity** | `core/self_match.py`, `ports/host_identity.py` | `match_self(nodes, hostname, hw_uuid) → Node` | A-007 |
| **tb_probe** | `ports/thunderbolt.py`, `core/tb_parse.py`, `core/iface_map.py` | `list_ports() → list[ThunderboltPort]`; `map_receptacle_to_iface(...)` | A-001, A-002, A-039 |
| **net_mutate** | `ports/network.py`, `core/heal_plan.py` | `ensure_bridge_and_ip(desired) → HealResult`; idempotent | A-009–A-013, A-038, A-041 |
| **reachability** | `ports/ping.py`, `ports/ssh_probe.py` (opt) | `ping(ip, timeout) → ReachabilityCheck` | A-018, A-032, A-045 |
| **topology** | `core/topo_match.py` | `build_topology(cfg, ports, links) → Topology` | A-022, A-023; OP-7: `complete` = alle Peers ping- oder domain-matchbar |
| **health** | `core/health.py` | `snapshot(...) → HealthSnapshot`; overall + Exit | A-018–A-020 |
| **doctor** | `core/doctor.py` | `run_checks(...) → DoctorReport` + Exit A-X2 | A-024, A-026 |
| **bench** | `ports/iperf.py`, `commands/bench.py` | optional PATH-Check | A-025, A-026 |
| **service** | `service/launchagent.py` | install/uninstall/status; KeepAlive | A-015–A-017 |
| **lock** | `ports/lock.py` | exclusive mutate lock | A-031, NFA-009 |
| **render** | `render/text.py`, `render/json_out.py`, `render/rich_monitor.py` | Plain first; rich optional | A-021, A-033, A-037 |
| **audit** | `ports/audit_log.py` | opt-in append + rotation | A-036 |
| **subprocess** | `ports/run_cmd.py` | zentral: argv list, timeout, no shell | A-044, A-045 |

### 3.3 CLI-Unterbefehle → Module

| Befehl | Command-Modul | Primäre Core/Ports | Exit (AD-3) |
|---|---|---|---|
| `tb` | `commands/tb.py` | tb_probe, render | 0 / 1 |
| `init` | `commands/init.py` | config_store, host_identity | 0 / 2 |
| `config show\|validate` | `commands/config_cmd.py` | config_* | 0 / 2 |
| `up` | `commands/up.py` | heal_plan, network, lock, platform | 0 / 1 / 2 / 3 |
| `heal` [`--loop`] | `commands/heal.py` | wie up + loop sleep | 0 / 1 / 2 / 3 |
| `status` | `commands/status.py` | health, ping, tb | 0 / 1 / 2 / 3 |
| `monitor` | `commands/monitor.py` | health loop + render | 0 (Ctrl+C) / 1 |
| `topo` | `commands/topo.py` | topology, tb | 0 / 1 / 2 |
| `doctor` | `commands/doctor.py` | doctor checks | 0 / 1 / 2 / 3 |
| `bench` | `commands/bench.py` | iperf | 0 / 1 / 2 |
| `service install\|uninstall\|status` | `commands/service_cmd.py` | launchagent | 0 / 1 / 2 |

### 3.4 Domänenabbildung (Kurz)

| Entität (DOMAENENMODELL) | Code | Persistenz |
|---|---|---|
| ClusterConfig | `models.ClusterConfig` | TOML |
| Node | `models.Node` | in Config; `role` runtime |
| ThunderboltPort / Link | `models.ThunderboltPort` / `ThunderboltLink` | Live |
| BridgeInterface | `models.BridgeInterface` | Live |
| Topology | `models.Topology` | abgeleitet |
| HealthSnapshot | `models.HealthSnapshot` | flüchtig |
| ServiceState | `models.ServiceState` | LaunchAgent |
| HealAction / DoctorFinding / BenchResult | models | Ausgabe / optional Log |

### 3.5 Konfigurationsstrategie

```
Priorität Config-Pfad:
  1. CLI  --config PATH
  2. Env  MACCLUSTER_CONFIG
  3. Default  ~/.config/maccluster/cluster.toml

Secrets: keine im Produkt.
Env (nicht secret):
  MACCLUSTER_CONFIG   — Config-Pfad
  NO_COLOR            — Farben aus (NFA-034)
  MACCLUSTER_RICH=0   — rich erzwingen aus (optional)
```

Beispiel-TOML-Struktur (Schema v1):

```toml
schema_version = 1
name = "studio-cluster"
subnet = "10.42.0.0/24"
bridge_interface = "bridge0"   # oder dokumentierter Mini-Default / Override
heal_interval_seconds = 30
ssh_probes_enabled = false

[[nodes]]
id = "node-a"
hostnames = ["mac-mini-a.local", "mac-mini-a"]
ip = "10.42.0.1"
hw_uuid = "00000000-0000-0000-0000-000000000001"

# … node-b … node-d (2–4)
```

### 3.6 Fehler- und Exit-Strategie

| Code | Bedeutung | Verwendung |
|---|---|---|
| 0 | ok / healthy | Erfolg; Monitor sauber beendet |
| 1 | error | OS-Fail, Rechte, Runtime, iperf missing (bench) |
| 2 | usage | Args, Config-Validierung, unsupported platform (mutate) |
| 3 | degraded | Peer down; up ohne TB-Link aber IP gesetzt; doctor warn-cluster |

- User-Fehler → **keine** Tracebacks als Normalfall; `-v` darf Stack zeigen.
- Mutierende Ops: bei fehlenden Rechten Exit **1**, Meldung `admin/sudo required` (A-028).
- Mapping-Ambiguität: mutate fail-closed Exit **2** (A-039).
- Alle Subprocesses: Timeout; bei Timeout Peer/Check = down/fail, kein Hang (A-045).

### 3.7 Privilegien

| Befehl | Root? |
|---|---|
| `tb`, `status`, `monitor`, `topo`, lesender `doctor`, `config show/validate` | nein |
| `init` (Home-Config) | nein |
| `up`, `heal` (Korrektur) | oft ja (Netz) — klar melden |
| `service install/uninstall` | User-Domain; i. d. R. ohne root für Plist |

**Kein setuid-Helper in v1** (OP-5): wenn Bridge root braucht, läuft Operator `sudo maccluster up|heal` interaktiv; LaunchAgent dokumentiert Einschränkung (nach Login, gleiche User-Session) und meldet fehlende Rechte statt still zu scheitern. Root-Helper nur als späteres ADR, falls Abnahme es erzwingt.

---

## 4. Teststrategie

Angelehnt an CLI-Skelett + NFA-048/049 + QUALITAET §3.

### 4.1 Ebenen

| Ebene | Was | Wo | HW-Bedarf |
|---|---|---|---|
| **Unit** | Config-Validate, Self-Match, TB-Parser, Receptacle→Iface-Map, Topo-Match, Heal-Plan (Soll/Ist → Actions), Exit-Aggregation, JSON-Schema-Felder | `tests/unit/` | nein |
| **Fixture-Integration** | Echte Sample-Outputs von `system_profiler`/`ioreg`/`ifconfig` als Dateien; Adapter lesen Fixtures statt Live | `tests/fixtures/`, `tests/integration/` | nein |
| **CLI-Golden / Smoke** | `python -m maccluster …` via subprocess; Exit-Codes + stdout snippets | `tests/cli/` | nein (mocks) |
| **Security** | Kein `shell=True`; Hostname/Pfad mit Sonderzeichen; keine Secrets in examples | `tests/unit/test_run_cmd.py` | nein |
| **Manuell / Abnahme** | 2–4 Node Mini, Reboot/Bridge-Loss (A-038), Monitor, service KeepAlive | `_fabrik/60-abnahme/` | ja |

### 4.2 Fixture-Fokus (CI ohne Live-Cluster)

1. **TB-Parse:** mind. ein Mac-mini-Sample (SPThunderboltDataType / ioreg dump) → Ports, speeds, unconnected.
2. **Mapping:** receptacle_id → interface_name; Ambiguitätsfall → fail-closed.
3. **Config:** 1/2/4/5 Nodes; doppelte IP; fehlendes `schema_version`; Self 0/1/2 Matches.
4. **Topo:** matched vs. unmatched peers; keine „plug cable“-Texte.
5. **Heal-Plan:** already configured → noop; missing IP → ensure_ip; no link → degraded flag.
6. **Doctor Exit:** worst error → 1; warn reachability → 3; optional iperf missing → nicht allein Exit 1.

### 4.3 Mock-Ports

OS-Aufrufe nur über `ports/*`. Tests injizieren Fake-Adapter:

```text
HostIdentityPort, ThunderboltPort, NetworkPort, PingPort,
LaunchctlPort, IperfPort, ClockPort (für Loop-Intervalle)
```

### 4.4 Verify-Kette (Welle 1)

```bash
make verify   # oder: ruff check + ruff format --check + pytest
```

README dokumentiert einen Befehl (QUALITAET G5).

### 4.5 Abdeckungs-Richtwert

- Geschäftslogik in `core/` ≥ 80 % Unit-Abdeckung (QUALITAET).
- Jeder behobene Bug → Regressionstest.
- Kein Live-4-Node in CI (Brief ANNAHME 21).

---

## 5. Vollständiger Verzeichnisbaum (Produkt-Repo)

Pfade relativ zu `projects/maccluster/` (Produktwurzel, **ohne** `_fabrik/`-Internals hier zu mischen). Englische Bezeichner im Code.

```text
maccluster/                          # Produkt-Root (= Git-Repo-Root des Produkts)
├── README.md                        # EN: install, commands, exit codes, config path, mapping
├── LICENSE                          # MIT
├── CHANGELOG.md
├── pyproject.toml                   # package metadata, scripts, optional rich extra
├── requirements.txt                 # optional pin / oder nur lock
├── requirements-dev.txt             # pytest, ruff, pip-audit
├── uv.lock | requirements.lock      # Lockfile (G2) — eines der Formate
├── Makefile                         # verify, test, lint
├── install.sh                       # optional convenience: pipx/pip install
├── .gitignore
├── .github/
│   ├── workflows/
│   │   └── ci.yml                   # ruff + pytest on push/PR
│   └── dependabot.yml               # pip ecosystem
├── examples/
│   └── cluster.toml                 # 4-node placeholders 10.42.0.1–.4
├── docs/
│   ├── faq/
│   │   ├── USER.md
│   │   ├── ADMIN.md
│   │   ├── AUTHOR.md                # N/A kurz oder gestrichen mit Hinweis
│   │   └── DEVELOPER.md
│   └── receptacle-mapping.md        # Mini TB port → iface notes + override
├── src/
│   └── maccluster/
│       ├── __init__.py              # __version__
│       ├── __main__.py              # python -m maccluster
│       ├── cli.py                   # argparse root + dispatch
│       ├── exitcodes.py             # 0/1/2/3 constants + helpers
│       ├── platform.py              # macOS Apple Silicon guard
│       ├── models.py                # dataclasses (domain entities)
│       ├── constants.py             # defaults: subnet, intervals, paths, labels
│       ├── commands/
│       │   ├── __init__.py
│       │   ├── tb.py
│       │   ├── init_cmd.py          # name init_cmd to avoid shadowing
│       │   ├── config_cmd.py
│       │   ├── up.py
│       │   ├── heal.py
│       │   ├── status.py
│       │   ├── monitor.py
│       │   ├── topo.py
│       │   ├── doctor.py
│       │   ├── bench.py
│       │   └── service_cmd.py
│       ├── core/
│       │   ├── __init__.py
│       │   ├── config_load.py       # parse TOML → models (pure given text)
│       │   ├── config_validate.py
│       │   ├── self_match.py
│       │   ├── tb_parse.py          # parse profiler/ioreg text → ports/links
│       │   ├── iface_map.py         # receptacle → interface (fixture-tested)
│       │   ├── heal_plan.py         # desired vs actual → HealAction list
│       │   ├── topo_match.py
│       │   ├── health.py
│       │   └── doctor.py
│       ├── ports/
│       │   ├── __init__.py
│       │   ├── run_cmd.py           # safe subprocess
│       │   ├── config_store.py      # read/write files, 0600, backup on --force
│       │   ├── host_identity.py     # hostname, hw_uuid
│       │   ├── thunderbolt.py       # invoke system_profiler/ioreg
│       │   ├── network.py           # ifconfig/networksetup get/set
│       │   ├── ping.py
│       │   ├── ssh_probe.py         # optional BatchMode
│       │   ├── lock.py
│       │   ├── audit_log.py
│       │   └── iperf.py
│       ├── render/
│       │   ├── __init__.py
│       │   ├── text.py              # plain tables/symbols (no color required)
│       │   ├── json_out.py          # schema_version envelopes
│       │   └── rich_monitor.py      # optional; importlib guard
│       └── service/
│           ├── __init__.py
│           ├── launchagent.py       # plist generate, launchctl load/unload
│           └── plist_template.py
└── tests/
    ├── conftest.py                  # fake ports, tmp config paths
    ├── unit/
    │   ├── test_config_validate.py
    │   ├── test_self_match.py
    │   ├── test_tb_parse.py
    │   ├── test_iface_map.py
    │   ├── test_heal_plan.py
    │   ├── test_topo_match.py
    │   ├── test_health_exit.py
    │   ├── test_doctor_exit.py
    │   ├── test_run_cmd_security.py
    │   └── test_exitcodes.py
    ├── integration/
    │   ├── test_config_roundtrip.py
    │   ├── test_tb_fixture_sample.py
    │   └── test_status_with_fakes.py
    ├── cli/
    │   ├── test_help.py
    │   ├── test_init_no_overwrite.py
    │   └── test_json_status_shape.py
    └── fixtures/
        ├── system_profiler_tb_mini.json.txt
        ├── ioreg_tb_sample.txt
        ├── ifconfig_bridge_ok.txt
        ├── ifconfig_missing_ip.txt
        └── cluster_valid_4.toml
```

**Dateibesitz für Wellen (Vorschlag, disjunkt):**

| Welle | Besitz (Beispiele) |
|---|---|
| W1 Gerüst | `pyproject.toml`, `LICENSE`, CI, `cli.py` skeleton, `Makefile`, leere packages, `tests/cli/test_help.py` |
| W2 Config/Identity | `core/config_*`, `self_match`, `ports/config_store`, `host_identity`, `commands/init|config` |
| W3 TB + Mapping | `tb_parse`, `iface_map`, `ports/thunderbolt`, `commands/tb`, fixtures |
| W4 up/heal/lock | `heal_plan`, `network`, `lock`, `commands/up|heal`, audit optional |
| W5 status/monitor/topo | `health`, `topo_match`, `ping`, render, monitor |
| W6 doctor/bench/service/json | doctor, iperf, launchagent, `--json`, polish |

---

## 6. Stärken

1. **Maximal boring:** Python + stdlib + OS-CLIs; ein Package, kein Server, keine DB.
2. **Muss-Scope vollständig abbildbar:** alle A-001–A-045 und AD-1…AD-6 passen in den Schnitt.
3. **Testbar ohne 4-Node-Farm:** Parser/Mapping/Heal-Plan hinter Ports + Fixtures (NFA-048).
4. **Symmetrie trivial:** ein Artefakt, Rollen nur runtime aus Identity.
5. **Least Privilege:** Read-only ohne Root; Mutation klar getrennt.
6. **Wellenfähig:** Module/Dateien disjunkt schneidbar.
7. **Offline & zero cloud:** keine Telemetrie, keine WAN-Pflicht (NFA-015/029).
8. **Optional rich/iperf/SSH:** Kern ohne optionale Tools vollständig.

---

## 7. Schwächen

1. **macOS-CLI-Fragilität:** `system_profiler`/`ioreg`-Formate können zwischen OS-Versionen driften (R-T01/R-D01) — Fixtures und defensive Parser nötig, aber kein stabiles Public-API.
2. **Privilegien-Modell ohne Helper:** LaunchAgent im User-Domain kann Bridge nach Reboot nicht setzen, wenn OS root verlangt — best-effort eingeschränkt (OP-5 Rest-Risiko).
3. **Kein typsicheres Config-Schreiben ohne kleine Dep oder Templates:** `tomllib` ist read-only; Schreiben über Templates oder `tomli-w`.
4. **Python-Kaltstart:** NFA-007 (< 1,5 s) ist erreichbar, aber empfindlich bei schweren Imports — deshalb kein eager `rich`.
5. **Ping-only Peer-Sicht ohne SSH:** Topo/Peer-Details remote dünn (bewusst, AD-2).

---

## 8. Risiken (entwurfsspezifisch)

| ID | Risiko | Gegenmaßnahme im Entwurf |
|---|---|---|
| ER-1 | Receptacle→Iface-Mapping falsch | Isoliertes `iface_map` + Fixtures + Config-Override + fail-closed mutate (A-039) |
| ER-2 | ifconfig/networksetup Semantik | Schmaler `NetworkPort`; Idempotenz-Tests; nur TB/Bridge-Targets (A-009) |
| ER-3 | Parallel up/heal Race | File-Lock `ports/lock.py` (A-031) |
| ER-4 | Subnetz-Kollision Heim-LAN | Default `10.42.0.0/24`; doctor warnt Route-Overlap (A-X7) |
| ER-5 | Heal ohne ausreichende Rechte im Agent | Klare Meldung; README sudo-Pfad; kein silent success |
| ER-6 | CI nur Linux | Reine core-Tests OS-agnostisch; platform-Guards unit-testen; manuelle Mini-Abnahme |
| ER-7 | Dependency Creep | pyproject: runtime deps leer oder nur optional `[rich]`; SCA Gate |

---

## 9. NFA-Tauglichkeit (Kurzcheck)

| NFA-Gruppe | Entwurf deckt ab durch |
|---|---|
| Performance 001–003 | Sequenzielle lokale Probes, Timeouts, schlanker Monitor-Loop |
| Skalierung 008–011 | Validate 2–4; kein Server; single writer lock |
| Verfügbarkeit 012–016 | heal + LaunchAgent KeepAlive; idempotent ensure_* |
| Security 018–022 | OS-Auth only; argv subprocess; no secrets |
| Datenschutz 028–030 | nur Tech-Felder; local files |
| A11y 032–035 | text symbols; NO_COLOR; plain without rich |
| Plattform 040–043 | platform.py guard; pipx; one package |
| Test 048–049 | ports + fixtures |

---

## 10. Abgrenzung zu alternativen Entwürfen (für Jury)

Dieser pragmatische Entwurf **verzicht bewusst** auf:

- Go/Swift-Rewrite (höhere Tooling-Komplexität; Brief verlangt Python),
- Daemon mit gRPC/Unix-Socket zwischen UI und Privileged Helper,
- Plugin-Architektur pro Probe,
- SQLite-Historie als Default,
- Kubernetes-/Fleet-ähnliche Control Plane.

Alles, was über „CLI + Datei + LaunchAgent“ hinausgeht, ist für v1 **out of style** dieses Entwurfs.

---

## 11. Offene Punkte an Gate / Folgearbeitsadrs

| Punkt | Vorschlag dieses Entwurfs |
|---|---|
| OP-7 Topology.complete | Peer ping-erreichbar **oder** Domain/Link-Match → complete; kein Vollmesh-Kabelzwang |
| OP-8 min. macOS | README: getestete Version(en); älter = warn best-effort in doctor |
| OP-5 Root nach Reboot | v1: User-Agent + dokumentierter sudo; Root-Helper nur bei Abnahme-Druck als ADR |
| TOML write | Prefer template write for `init` (stdlib-only); avoid extra dep unless needed |
| LaunchAgent Label | `ai.maccluster.heal` (final in STACK/ADR) |

---

## 12. Fazit

**ENTWURF-pragmatisch** ist der Standard-Monolith für MacCluster: Python 3.11+, argparse, TOML-Config, schmale OS-Ports, reine Core-Logik, optionales rich, LaunchAgent für Heal. Er erfüllt die Brief-Stack-Vorgabe, hält die beweglichen Teile minimal und ist mit Fixture-CI abnahmefähig — passende Wahl, wenn Jury **Einfachheit und Betriebsaufwand** hoch gewichtet.
