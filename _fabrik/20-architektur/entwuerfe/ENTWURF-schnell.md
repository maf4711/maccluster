# ENTWURF-schnell — MacCluster

| Feld | Wert |
|---|---|
| Projekt | MacCluster (`maccluster`) |
| Phase | 2 ARCHITEKTUR — Entwurfsvariante |
| Leitlinie | **Time-to-MVP zuerst** |
| Stand | 2026-08-01 |
| Quelle | `_fabrik/00-intake/BRIEF.md`, `_fabrik/10-analyse/*` |
| Status | Ernsthafter Alternativentwurf (nicht finaler Sieger) |

---

## 1. Leitidee

Ein **monolithisches Python-CLI-Package** mit maximalem Generierungsgrad und minimalen beweglichen Teilen:

- **Ein Prozess, ein Package, ein Entry-Point** (`maccluster`)
- **stdlib primär** + **Typer** (CLI) + **tomllib** (Config lesen) + optional **rich** (extras)
- **Keine** Daemon-Frameworks, **keine** Plugin-Runtime, **kein** Message-Bus, **keine** DB
- OS-Tools hinter **dünnen Adapter-Funktionen** (argv-separiert, timeout), mockbar für CI
- Mutierende Ops (`up`/`heal`) teilen denselben **Ensure-Pfad** (Idempotenz = Code-Reuse)
- LaunchAgent ist **Plist-Template + launchctl-Wrapper**, kein separater Service-Binary-Split

**Ziel:** Alle **Muss**-Anforderungen (A-001–A-045, MoSCoW Muss) und die **Soll**-Pfade des Vollausbaus tragfähig umsetzen — mit bewusst akzeptierten Abkürzungen, die in §8 dokumentiert sind.

---

## 2. Architekturstil

| Aspekt | Wahl |
|---|---|
| Stil | Modularer Monolith (Library-Layout), CLI-first |
| Deployment | Ein Package auf jedem Member (Symmetrie) |
| Persistenz | Dateien: TOML-Config, optional Lock/Log |
| Kommunikation | Keine inter-process IPC außer LaunchAgent → CLI-Subprocess |
| Parallelität | Single-Writer-Lock pro Host; Lesen frei parallel |
| Generierung | Hoher Code-Anteil aus Templates (Plist, Beispiel-TOML, JSON-Schemas als Dicts) |

```
┌─────────────────────────────────────────────────────────────┐
│                     maccluster (CLI)                        │
│  typer app → commands/* → domain services → os adapters     │
└───────────────┬─────────────────────┬───────────────────────┘
                │                     │
        ┌───────▼───────┐     ┌───────▼────────┐
        │ cluster.toml  │     │ macOS HostOS   │
        │ (~/.config/…) │     │ profiler/ifcfg │
        └───────────────┘     │ ping/launchctl │
                              │ iperf3/ssh opt │
                              └────────────────┘
```

---

## 3. Komponenten (dateischarf)

Komponentengrenzen = **Package-Pfade** unter `src/maccluster/`. Wellen können disjunkten Dateibesitz zuweisen.

| ID | Komponente | Package-Pfad | Verantwortung | Muss-A |
|---|---|---|---|---|
| C1 | **CLI Shell** | `cli.py`, `commands/*.py` | Typer-App, Flags (`--config`, `--json`, `-v`), Exit-Code-Mapping | A-028, A-033, A-043 |
| C2 | **Config** | `config/` | Load/validate/init/show; Pfad-Resolution AD-6; schema_version | A-003–A-008, A-027, A-040, A-042 |
| C3 | **Identity** | `identity.py` | Self-Match Hostname/HW-UUID; Platform-Guard | A-007, A-043 |
| C4 | **TB Probe** | `probes/thunderbolt.py`, `probes/mapping.py` | system_profiler/ioreg parse; Receptacle→iface | A-001, A-002, A-039 |
| C5 | **Net Probe** | `probes/network.py` | ifconfig/networksetup read; Bridge-Ist | A-009, A-018 |
| C6 | **Reachability** | `probes/ping.py`, `probes/ssh.py` | Ping (Muss-Pfad); SSH optional | A-018, A-032, A-045 |
| C7 | **Ensure/Heal** | `actions/ensure.py`, `actions/heal.py` | Bridge+IP setzen (idempotent); einmalig + loop | A-009–A-014, A-038, A-041 |
| C8 | **Lock** | `lock.py` | File-Lock mutierende Ops; stale PID | A-031 |
| C9 | **Service** | `service/launchagent.py` | install/uninstall/status User-Domain | A-015–A-017 |
| C10 | **Health/Topo** | `health.py`, `topo.py` | Snapshot, Aggregation, Map | A-018–A-023 |
| C11 | **Doctor/Bench** | `doctor.py`, `bench.py` | Checks; iperf3 optional | A-024–A-026 |
| C12 | **Render** | `render/text.py`, `render/json_out.py`, `render/rich_opt.py` | Plaintext first; JSON; optional rich | A-021, A-033, A-037 |
| C13 | **Subprocess** | `osutil/run.py` | argv-only, timeout, no shell | A-044, A-045 |
| C14 | **Models** | `models.py` | Dataclasses: ClusterConfig, Node, … | Domäne |

### 3.1 Abhängigkeitsrichtung (streng)

```
commands → (config | identity | health | topo | doctor | actions | service | probes)
actions  → config, identity, probes/network, probes/thunderbolt, lock, osutil
health   → config, probes/*, models
render   → models   (keine OS-Aufrufe)
osutil   → stdlib only
```

Keine Zyklen. `commands/*` orchestriert nur; Business-Regeln in `actions`/`health`/`config`.

---

## 4. Verzeichnisstruktur (Produkt-Repo)

```
maccluster/                          # projects/maccluster/ (Produktroot)
├── pyproject.toml                   # package, scripts, optional [rich]
├── README.md                        # EN: install, config, commands, exit codes
├── LICENSE
├── CHANGELOG.md
├── install.sh                       # thin pipx/pip helper
├── Makefile                         # verify = lint + test
├── .github/
│   ├── workflows/ci.yml
│   └── dependabot.yml
├── examples/
│   └── cluster.toml                 # 4-node placeholders
├── src/maccluster/
│   ├── __init__.py
│   ├── __main__.py
│   ├── cli.py                       # Typer root
│   ├── exitcodes.py                 # 0/1/2/3 constants + helpers
│   ├── models.py
│   ├── identity.py
│   ├── lock.py
│   ├── health.py
│   ├── topo.py
│   ├── doctor.py
│   ├── bench.py
│   ├── config/
│   │   ├── __init__.py
│   │   ├── paths.py                 # AD-6 resolution
│   │   ├── load.py
│   │   ├── validate.py
│   │   └── init_cmd.py
│   ├── commands/
│   │   ├── tb.py
│   │   ├── init.py
│   │   ├── config_cmd.py
│   │   ├── up.py
│   │   ├── heal.py
│   │   ├── status.py
│   │   ├── monitor.py
│   │   ├── topo_cmd.py
│   │   ├── doctor_cmd.py
│   │   ├── bench_cmd.py
│   │   └── service_cmd.py
│   ├── actions/
│   │   ├── ensure.py                # shared up/heal mutation
│   │   └── heal.py                  # one-shot + loop
│   ├── probes/
│   │   ├── thunderbolt.py
│   │   ├── mapping.py
│   │   ├── network.py
│   │   ├── ping.py
│   │   └── ssh.py
│   ├── service/
│   │   ├── launchagent.py
│   │   └── plist_template.py
│   ├── osutil/
│   │   └── run.py
│   └── render/
│       ├── text.py
│       ├── json_out.py
│       └── rich_opt.py
├── tests/
│   ├── fixtures/                    # system_profiler XML/JSON samples
│   ├── unit/
│   └── integration/                 # mocked subprocess
└── docs/                            # optional short operator notes
```

**Dateischarfe Wellen-Eignung:** z. B. Welle 1 Gerüst+config+exitcodes; Welle 2 probes+tb+topo; Welle 3 ensure/up/heal; Welle 4 status/monitor/doctor; Welle 5 service/bench/json/ssh.

---

## 5. CLI-Schnittstelle

Entry: `maccluster = maccluster.cli:app` (Typer).

| Befehl | Mutation | Root? | Kernverhalten |
|---|---|---|---|
| `tb` | nein | nein | Ports/Links anzeigen |
| `init` | Config-Datei | nein | Vorlage schreiben; `--force` + `.bak` |
| `config show` | nein | nein | Config anzeigen |
| `config validate` | nein | nein | Validierung Exit 0/2 |
| `up` | ja | oft ja | ensure bridge+IP; Exit 3 ohne TB-Link |
| `heal` [`--loop`] | ja | oft ja | Drift korrigieren; Loop Default 30 s |
| `status` | nein | nein | Snapshot; Exit 3 bei peer down |
| `monitor` | nein | nein | Refresh 1–2 s; Ctrl+C → 0 |
| `topo` | nein | nein | Link-Map + Config-Match |
| `doctor` | nein | nein | Checkliste; Exit worst-check |
| `bench` | nein* | nein | iperf3 optional (*Last, kein Netz-Config) |
| `service install\|uninstall\|status` | ja (plist) | ggf. | User LaunchAgent |

Globale Flags:

- `--config PATH` > Env `MACCLUSTER_CONFIG` > `~/.config/maccluster/cluster.toml`
- `--json` (status, tb, topo, doctor; schema_version)
- `-v` / `--verbose` (Kann-Niveau, einfach: stderr debug)
- `NO_COLOR` respektieren

### 5.1 Exit-Codes (AD-3)

| Code | Bedeutung |
|---|---|
| 0 | ok / healthy / sauberer Monitor-Abbruch |
| 1 | Runtime/System/Rechte/OS-Befehl |
| 2 | Usage/Config/Validierung/unsupported mutate |
| 3 | Degraded (peer down, up ohne Link, doctor warn-cluster) |

---

## 6. Datenmodell-Abbildung

| Domäne | Python | Persistenz |
|---|---|---|
| `ClusterConfig` | `@dataclass` + TOML round-trip (write via simple emitter oder `tomli-w` optional — **Abkürzung:** handgeschriebenes Template + tomllib read) | `cluster.toml` |
| `Node` | dataclass; `role` runtime-only | in Config |
| `ThunderboltPort/Link` | dataclass | live |
| `BridgeInterface` | dataclass | live |
| `HealthSnapshot` | dataclass | flüchtig / `--json` |
| `Topology` | dataclass | flüchtig |
| `ServiceState` | dataclass | LaunchAgent-Fakten |
| `DoctorFinding` | dataclass | Ausgabe |
| `HealAction` | dataclass | optional Log |

### 6.1 Config-Schema v1 (skizze)

```toml
schema_version = 1
name = "lab-cluster"
subnet = "10.42.0.0/24"
bridge_interface = "bridge0"   # override if needed
heal_interval_seconds = 30
ssh_probes_enabled = false
# action_log_enabled = false   # Kann

[[nodes]]
id = "node-a"
hostnames = ["mac-mini-a.local", "mac-mini-a"]
ip = "10.42.0.1"
hw_uuid = "00000000-0000-0000-0000-000000000001"

[[nodes]]
id = "node-b"
# ...
```

Validierung hart: 2–4 Nodes, unique id/ip/hw_uuid, IP ∈ subnet, self-match genau 1, schema_version == 1, interface name `[A-Za-z0-9_-]+`.

---

## 7. Datenfluss (Kernpfade)

### 7.1 `up` / `heal` (gemeinsam)

```
CLI → resolve config path → load+validate
    → platform guard (mutate)
    → acquire lock
    → identity.self
    → net.probe bridge + tb.probe link
    → ensure.desired(bridge, self.ip)   # idempotent steps
    → if no TB link: exit 3 else 0/1
    → release lock
```

**Ensure-Schritte (lokal only, A-041):**

1. Resolve target interface (config override > mapping > fail closed)
2. Ensure interface exists / bridge present (allowlisted OS calls)
3. Ensure admin-up
4. Ensure Self-IP present (remove stale cluster IP on same iface if drifted)
5. Report step list (partial → Exit 1, not silent 0)

### 7.2 `status` / `monitor`

```
load config → self → parallel-ish ping peers (timeout ≤1–2s, budget <3s)
           → tb links (best-effort, skip if slow)
           → aggregate HealthSnapshot → render text|json|rich
```

Monitor: sleep interval; reload config best-effort next tick (RF-A16 simplified: re-read file each tick).

### 7.3 `topo`

```
tb ports/links + config nodes → match peer by domain/hint/ip → Topology
complete := all peers reachable OR link-matched (OP-7 ANNAHME)
```

### 7.4 `service install`

```
resolve absolute path of maccluster binary
write ~/Library/LaunchAgents/com.maccluster.heal.plist
launchctl bootstrap gui/$(id -u) …
ProgramArguments: [bin, "heal", "--loop", "--config", path]
KeepAlive=true; ThrottleInterval ≥ 10
```

**Privilege-Abkürzung:** User-Agent ruft `heal` ohne eingebettetes sudo. Wenn Bridge root braucht: Heal meldet Exit 1 „admin required“ im Log; Operator führt einmalig `sudo maccluster up` nach Login aus oder startet elevateten Kontext manuell. Kein Root-Helper-Daemon in v1-schnell (siehe §8).

---

## 8. Stack-Skizze (Time-to-MVP)

| Schicht | Technologie | Version (Ziel) | Pflicht? |
|---|---|---|---|
| Language | Python | **3.11+** | ja |
| CLI | **Typer** | ≥0.12 | ja (Abkürzung vs. reines argparse: schneller Help/Subcommands) |
| CLI-Unterbau | Click (via Typer) | transitive | ja |
| Config read | **tomllib** (stdlib) | 3.11+ | ja |
| Config write | Template-String / manuelles Emit | — | ja (kein pydantic) |
| Models | **dataclasses** + typing | stdlib | ja |
| JSON | json stdlib | — | ja |
| TUI | **rich** | optional extra | nein |
| Tests | pytest | ≥8 | dev |
| Lint | ruff | aktuell | dev |
| Packaging | hatchling oder setuptools | pyproject | ja |
| OS tools | system_profiler, ioreg, ifconfig, networksetup, ping, launchctl, iperf3, ssh | macOS | runtime |

**Explizit nicht im Stack:** FastAPI/HTTP, SQLite, asyncio-Framework, poetry-only lock (uv/pip-tools ok), PyObjC, Swift-Bridge.

**Begründung Typer statt argparse:** Weniger Boilerplate für 12 Subcommands + Flags; Help EN out-of-the-box; gut generierbar. Dependency klein, MIT, reif.

**Begründung kein Pydantic:** Validierung handgeschrieben hält Dependency-Surface und Startzeit klein (NFA-007); Domain hat <15 Felder.

---

## 9. Fehler- und Konfigurationsstrategie

### 9.1 Fehler

| Klasse | Handling |
|---|---|
| User/Config | `UsageError` → Exit **2**, EN message, no traceback |
| Privilege | Exit **1**, „admin/sudo required“ + which step |
| OS/probe fail | Exit **1**, tool name + short stderr; timeouts → mark unknown/down |
| Degraded cluster | Exit **3**, full output still printed |
| Unexpected | catch at CLI boundary → Exit 1, optional `-v` traceback |

Kein interaktiver sudo-Prompt aus Library-Code; Operator elevatet die Shell.

### 9.2 Konfiguration

| Quelle | Inhalt | Secrets |
|---|---|---|
| `cluster.toml` | Cluster-Soll | **keine** Keys/Passwörter; nur optionale SSH-Zielstrings |
| Env `MACCLUSTER_CONFIG` | Config-Pfad | nein |
| Env `NO_COLOR` | Rendering | nein |
| LaunchAgent plist | Binary-Pfad, args, interval | keine Credentials |
| Optional action log | `~/.local/state/maccluster/actions.log` | no key material |

Secrets ausschließlich OS-Keychain/SSH-Agent des Operators — Produkt speichert nichts.

### 9.3 Lock

Pfad: `~/.local/state/maccluster/mutate.lock` (PID + timestamp).  
Stale: PID dead oder age > 120 s → übernehmen.  
Gilt für: `up`, `heal`, `service install/uninstall`.

---

## 10. Teststrategie (CI ohne Live-HW)

| Ebene | Was | Wie |
|---|---|---|
| Unit | Config validate, self-match, mapping parse, topo match, exit aggregation | fixtures |
| Unit | ensure idempotency pure logic | fake NetProbe |
| Integration | CLI invoke via Typer CliRunner | mocked `osutil.run` |
| Contract | `--json` schema_version + fields | snapshot |
| Manuell | 2–4 Node Abnahme, reboot heal | Checkliste 60-abnahme |

Fixtures: mind. 1× `system_profiler` SPThunderboltDataType Sample (Mac mini), partial mesh, empty ports, garbage XML.

---

## 11. Abdeckung Muss-Anforderungen (tragfähig)

| A-ID | Wie dieser Entwurf es trägt |
|---|---|
| A-001/002 | C4 + `tb` command, no root |
| A-003–008,040,042 | C2 init/validate/show, schema_version, AD-6 paths |
| A-009–012 | C7 ensure + privilege check + Exit 3 no link |
| A-013/038 | heal one-shot = ensure; post-reboot via service/loop |
| A-018–021 | C10 + C12 plaintext |
| A-022/023 | C10 topo, no cable advice |
| A-024 | C11 doctor checks |
| A-027–030 | load errors, RO paths, degraded |
| A-034/035 | single package, offline probes |
| A-039 | mapping.py + fixtures + fail closed |
| A-041 | ensure only local |
| A-043 | identity platform guard |
| A-044 | osutil.run argv-only |

**Soll** (Vollausbau): service C9, heal loop, `--json`, bench, SSH optional — im gleichen Monolith, keine zweite Architektur.

**Kann:** action log + rich extra — stubs erlaubt, Default aus.

---

## 12. Stärken

1. **Time-to-MVP:** Ein Package, generierbare Command-Dateien, geteilter Ensure-Pfad → wenig Doppelimplementierung.
2. **Einfachheit:** Keine verteilte Koordination, kein Daemon-Framework; LaunchAgent startet dasselbe CLI.
3. **Testbarkeit:** Adapter `osutil.run` + Fixtures decken NFA-048 ohne TB-Farm.
4. **Symmetrie:** Identische Binary/Config-Struktur auf allen Members.
5. **Security-Baseline erreichbar:** argv-Subprocess, keine Secrets, least privilege RO.
6. **Wellenfähig:** dateischarfe Module (commands vs probes vs actions).
7. **NFA-Performance:** synchrone Probes mit Timeouts; 4 Nodes trivial unter 3 s.

---

## 13. Schwächen

1. **Privilege-Lücke User-Agent:** Heal im LaunchAgent kann ohne Root Bridge/IP nach Reboot **nicht** setzen, wenn macOS Elevation verlangt — A-038 hängt dann an manuellem `sudo up`/`sudo heal` oder Login-Session mit Rechten.
2. **TB-Parser-Fragilität:** stdlib-XML/Text-Parse ohne harte Multi-OS-Fixture-Matrix riskiert R-F01/R-D01.
3. **Typer-Dependency:** gegen reines stdlib-argparse zusätzliche Supply-Chain (mitigiert: eine reife Lib).
4. **Geringe Abstraktion Network-Apply:** ensure ist prozedural; macOS-API-Drift trifft zentrale Datei hart.
5. **Topo-Konfidenz einfach:** Matching heuristisch (Domain/IP); R-F02 bleibt.
6. **Kein dry-run-Framework** out-of-the-box (kann Flag später; Abkürzung).
7. **Monitor-Refresh** blockierend im Main-Thread — bei langsamem system_profiler Skip/Timeout nötig (NFA-002).

---

## 14. Risiken (entwurfsspezifisch)

| Risiko | Bezug | Mitigation in diesem Entwurf | Rest |
|---|---|---|---|
| Root-Heal unmöglich im User-Agent | R-T02, A-038, AD-4 | Doku + doctor check „heal needs admin“; einmaliges sudo up; optional später Root-Helper-ADR | **Hoch** — Gate-relevant |
| Falsches Interface | R-T01, A-039 | fail closed; config override; fixtures | Mittel |
| ifconfig Schaden | R-D02, R-T04 | allowlist iface; nur Self-IP; no default route | Mittel |
| Heal-Races multi-node | R-F03 | nur lokal mutieren; idempotent | Niedrig |
| Doppel-heal local | R-T03, A-031 | file lock | Niedrig |
| CI ohne HW | R-T05 | fixtures + manuelle Abnahme | Mittel |
| Erwartung HA | R-F05 | Help/README best-effort | Niedrig |

---

## 15. Bewusst akzeptierte Abkürzungen (Time-to-MVP)

| # | Abkürzung | Konsequenz | Akzeptabel weil |
|---|---|---|---|
| K1 | Kein Root-Helper / privileged helper tool | LaunchAgent-Heal ggf. ohne Netz-Mutation | AD-4 User-Agent; A-038 best-effort; klar melden |
| K2 | Typer statt reinem argparse | 1 Dependency | Reif, MIT, schneller Delivery |
| K3 | Kein Pydantic / keine JSON-Schema-Lib | Handvalidierung | Kleines Schema |
| K4 | Config-Write als Template, nicht generischer TOML-Writer | Kommentare in Vorlage limitiert | init + force reichen |
| K5 | Ensure prozedural, kein State-Machine-Framework | Logik in einer Datei | 3 Bridge-Zustände |
| K6 | SSH nur dünner Wrapper, Default aus | Wenig Remote-Diagnose | AD-2 |
| K7 | Bench startet keinen Auto-Server auf Peer | Operator startet iperf3 -s manuell oder `--server` lokal | RF-A18 |
| K8 | Action-Log / Rich nur minimal oder extras | Kann-Features dünn | MoSCoW Kann |
| K9 | Topology.complete = Ping∨Link-Match | Kein Graph-SPF | OP-7 |
| K10 | Ein Lock-File, kein fcntl-cluster | Nur Single-Host | Spec: lokal |
| K11 | system_profiler primär, ioreg Fallback later | Dual-Source evtl. Phase-2 im Code | Fixtures first |
| K12 | Kein asyncio | sequentielle/timeout Probes | 4 Nodes |

---

## 16. ANNAHMEN dieses Entwurfs

| ID | Annahme |
|---|---|
| ES-1 | Python 3.11+ und pipx/pip auf Ziel-Minis verfügbar |
| ES-2 | `bridge0` o. ä. als Default-Name dokumentiert; Override in Config |
| ES-3 | User LaunchAgent ist Default; Root-Helper **nicht** in v1-schnell |
| ES-4 | Subnetz-Default `10.42.0.0/24` (AD-1); doctor warnt Route-Overlap, up bricht nicht auto ab (A-X7) |
| ES-5 | Package-Name/Distribution: `maccluster` auf PyPI-lokal/private ok; Entry `maccluster` |
| ES-6 | macOS „aktuell stabil“; ältere Versionen best-effort Warnung |

---

## 17. Vergleichshinweis (für Jury)

Dieser Entwurf maximiert **Einfachheit** und **Time-to-MVP**. Ein Gegenentwurf (z. B. „robust“) würde typischerweise investieren in: privileged helper, strengere Probe-Pipeline (profiler+ioreg dual), reichere Topo-Konfidenz, dry-run, ggf. click-only/stdlib-only.

**Empfehlung Nutzung:** Als Baseline für schnellen Vollausbau geeignet, **wenn** Gate die Privilege-Abkürzung K1 für A-038 akzeptiert oder ein schmaler Root-Pfad nachgezogen wird.

---

## 18. Traceability Kurz

| Brief F | Komponenten |
|---|---|
| F1 TB-Info | C4, commands/tb |
| F2 Config | C2, commands/init, config_cmd |
| F3 up | C7, commands/up |
| F4 heal/service | C7, C9, commands/heal, service_cmd |
| F5 monitor/status | C10, C12, commands/status, monitor |
| F6 topo | C10, commands/topo_cmd |
| F7 doctor/bench | C11, commands/doctor_cmd, bench_cmd |
