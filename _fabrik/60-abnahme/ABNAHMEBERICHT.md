# Abnahmebericht — MacCluster

| Feld | Wert |
|---|---|
| Projektname | MacCluster |
| Slug | `maccluster` |
| Datum | 2026-08-01 |
| Auftraggeber | Produktmanagement (Intake) |
| Version / Stand | **0.1.0** |
| Quellen | Brief, ANFORDERUNGEN (Muss), TESTBERICHT, PROTOKOLL, README |
| Rolle | Abnahme-Dokumentierer |

---

## 1. Kurzfassung

MacCluster 0.1.0 ist ein CLI (`maccluster`) für **2–4 Apple Silicon Mac minis** über
Thunderbolt-Bridge. Vollausbau (Muss + Soll) ist implementiert. QA meldet **102/102**
pytest grün und CLI-Smoke auf einem Host-Mac mini. Alle **31 Muss-Anforderungen** sind
automatisiert und/oder per Smoke nachweisbar; Live-Root-`up`/`heal` und physisches
2–4-Node-Mesh liegen **außerhalb der CI** (NFA-048, akzeptiert).

**Gesamtempfehlung: freigeben mit Auflagen** (siehe §7).

---

## 2. Nachweis-Matrix (Muss A-xxx)

**Legende:** ERFÜLLT · TEILWEISE · OFFEN  
**Nachweis-Kürzel:** T = automatisierter Test · S = CLI-Smoke (TESTBERICHT §3) · D = Doku (README/docs)

| ID | Kurz | Status | Beweis |
|---|---|---|---|
| **A-001** | TB-Ports/Link-Info | **ERFÜLLT** | T: `tests/unit/commands/test_tb.py`, `adapters/test_tb_system_profiler.py`; S: `maccluster tb` → Ports, NO-LINK, Exit 0 |
| **A-002** | TB ohne Admin | **ERFÜLLT** | S: `tb` ohne sudo Exit 0; D: README Privileges |
| **A-003** | `init` | **ERFÜLLT** | T: `test_init_service`, `test_init_roundtrip`; S: TOML mit schema_version + Self |
| **A-004** | kein stilles Overwrite + Backup | **ERFÜLLT** | T: `test_init_no_overwrite`, `test_init_force_backup`; S: Exit 2 / `.bak` |
| **A-005** | config show/validate | **ERFÜLLT** | T: `config/test_validate.py`; S: show/validate |
| **A-006** | 2–4 Nodes hart | **ERFÜLLT** | T: `test_one_node`, `test_five_nodes` |
| **A-007** | Self-Node-Erkennung | **ERFÜLLT** | T: `test_identity*`, `test_identity_multi`; S: example Self-Mismatch Exit 2 |
| **A-009** | `up` Bridge+IP | **ERFÜLLT** | T: `test_mutate_service`, `test_heal_recovery` (Fake-Apply); Live-Root manuell |
| **A-010** | `up` idempotent | **ERFÜLLT** | T: `heal_logic/test_plan.py` already_configured |
| **A-011** | `up` ohne TB-Link → Exit 3 | **ERFÜLLT** | T: `test_already_configured_no_tb_link_degraded` |
| **A-012** | `up` ohne Rechte → Exit 1 | **ERFÜLLT** | T: `integration/test_mutate_guard.py`; S: PrivilegeError + `admin/sudo required` |
| **A-013** | heal einmalig | **ERFÜLLT** | T: shared Ensure-Pfad mit up; Plan-Tests |
| **A-018** | status Snapshot | **ERFÜLLT** | T: `test_status*`; S: Nodes + DOWN |
| **A-019** | status Peer-down Exit 3 | **ERFÜLLT** | T: `test_status_exit_codes`, `test_aggregate`; S: Exit 3 |
| **A-020** | live monitor | **ERFÜLLT** | T: `test_monitor_iterations` (Fake; kein TTY-E2E) |
| **A-021** | Plaintext / NO_COLOR | **ERFÜLLT** | T: `test_no_color`, `test_symbols`, `test_plain` |
| **A-022** | topo Map | **ERFÜLLT** | T: `test_json_tb_topo`, `topology/test_build`; S: complete=False |
| **A-023** | keine Kabel-Empfehlung | **ERFÜLLT** | T: `test_topo_no_cable_advice` |
| **A-024** | doctor Basis | **ERFÜLLT** | T: `test_doctor_*`; S: Checks, Exit 3 warn |
| **A-027** | Config fehlt/kaputt | **ERFÜLLT** | T: `test_config_missing`, `test_config_errors`; S: Exit 2 + Pfad/`init` |
| **A-028** | Admin klar melden | **ERFÜLLT** | T+S: up/heal Privilege; D: README |
| **A-029** | RO ohne Root | **ERFÜLLT** | S: tb/status/topo/doctor; D: README Trennung |
| **A-030** | Partial-Cluster robust | **ERFÜLLT** | T: `test_partial_*`, `test_partial_mesh` |
| **A-034** | Installation symmetrisch | **ERFÜLLT** | T: `test_install_entrypoints`; D: README Install/Workflow |
| **A-035** | Offline | **ERFÜLLT** | T: `test_offline_no_cloud_imports`, `test_no_network_clients` |
| **A-038** | Post-Reboot best-effort | **TEILWEISE** | T: `test_heal_recovery` (Bridge weg → apply, Fake); **kein Live-Reboot** in CI |
| **A-039** | Receptacle→Interface | **ERFÜLLT** | T: `test_receptacle`, `test_ambiguous_mapping`; D: `docs/receptacle-mapping.md` |
| **A-040** | Config-Pfad Override | **ERFÜLLT** | T: `config/test_paths.py`; D: README; S: missing path |
| **A-041** | nur lokale Mutation | **ERFÜLLT** | T/strukturell: mutate_service nur Self; kein Remote-Write |
| **A-042** | schema_version | **ERFÜLLT** | T: load/validate missing/unsupported; init schreibt 1 |
| **A-043** | Platform Guard | **ERFÜLLT** | T: `test_guard_*`, `test_platform_guard_cli` |
| **A-044** | Security argv/Secrets | **ERFÜLLT** | T: `test_process_runner`, `test_process_argv_special_chars`; example ohne Secrets |

### Muss-Bilanz

| Status | Anzahl |
|---|---|
| ERFÜLLT | **30** |
| TEILWEISE | **1** (A-038: logisch/Fake grün, Live-Reboot offen) |
| OFFEN | **0** |

### Soll (Stichprobe — nicht Abnahmeblocker)

| ID | Kurz | Nachweis | Anmerkung |
|---|---|---|---|
| A-008 | Beispiel-TOML | `examples/cluster.toml`, `test_example_cluster_toml` | ERFÜLLT |
| A-014 | heal --loop | `test_heal_loop_service` | ERFÜLLT (Fake) |
| A-015–A-017 | service | `test_service_*`, plist KeepAlive | Install/Uninstall Fake; KeepAlive-Restart live ungemessen |
| A-025–A-026 | bench | `test_bench*`, graceful missing iperf | ERFÜLLT |
| A-031 | Writer-Lock | `test_lock_file` | ERFÜLLT |
| A-032 | SSH optional | Adapter, Default aus | begrenzt automatisiert |
| A-033 | --json | `test_json_*`, Smoke | ERFÜLLT |
| A-045 | Timeouts | ProcessRunner | ERFÜLLT |

---

## 3. Probelauf-Anleitung (2–4 Minis)

Geführtes Szenario mit erwarteten Exits: [`ABNAHME-SZENARIO.md`](./ABNAHME-SZENARIO.md).

### 3.1 Voraussetzungen

- 2–4 Apple Silicon Mac minis, Thunderbolt-Kabel verbunden
- macOS, Python 3.11+, Admin-Rechte für `up`/`heal`
- Optional: `iperf3` im PATH für Bench

### 3.2 Install (jeder Node)

```bash
cd /path/to/maccluster   # oder ausgeliefertem Package
pipx install .           # oder: ./install.sh  |  python3 -m pip install -e .
maccluster --version     # → 0.1.0
maccluster --help
```

### 3.3 init + Config

```bash
# Node A (Self vorausgefüllt):
maccluster init
# Config editieren: hostnames, hw_uuid, IPs 10.42.0.1–.N (N=2..4)
# Dieselbe logische cluster.toml auf alle Members kopieren (Self wird pro Host aufgelöst)
maccluster config validate   # Exit 0 nur wenn Self matched
maccluster config show
```

Default-Pfad: `~/.config/maccluster/cluster.toml`  
Override: `--config` > Env `MACCLUSTER_CONFIG` > Default

### 3.4 Bring-up

```bash
sudo maccluster up       # Bridge + Self-IP; Exit 0 mit Link, Exit 3 ohne TB-Link aber IP gesetzt
maccluster status        # Peers: UP/DOWN; Exit 3 wenn ≥1 Peer down
maccluster topo
maccluster doctor
```

Auf **jedem** Member: `sudo maccluster up` (nur lokal, nie Remote-Write).

### 3.5 Monitor + Service

```bash
maccluster monitor                 # Ctrl+C → Exit 0
maccluster service install         # User LaunchAgent → heal --loop
maccluster service status          # installed/running, Label com.maccluster.heal
# optional: maccluster heal --loop  (Foreground, best-effort)
```

### 3.6 Recovery-Stichprobe (A-038)

Nach Reboot **oder** manuellem Entfernen der Bridge/IP:

```bash
sudo maccluster heal     # oder warten auf LaunchAgent-Tick
maccluster status        # Bridge/Self-IP wieder gemäß Config (best-effort, ≤120 s Ziel)
```

### 3.7 QA-Smoke (bereits durchgeführt, 2026-08-01)

| Schritt | Ist | Exit |
|---|---|---|
| help / tb / init / config | OK | 0 bzw. 2 bei Guardrails |
| status ohne Config | Pfad + init | 2 |
| status Peers down | degraded | 3 |
| up/heal ohne Root | admin/sudo required | 1 |
| service status | not installed | 0 |
| --json Fehler | JSON error | ≠0 |

Live-2-Node-Mesh mit sudo: **noch Auftraggeber-Probelauf** (Auflage).

---

## 4. Testbericht-Zusammenfassung

Vollständig: [`../50-qa/TESTBERICHT.md`](../50-qa/TESTBERICHT.md)

| Kennzahl | Wert |
|---|---|
| Suite | `python3 -m pytest -q` |
| Tests gesamt | **102** |
| Bestanden | **102** |
| Fehlgeschlagen | **0** |
| Offene Bugs ≥ mittel | **0** (kein BUGS.md) |
| CI-Strategie | Fixtures/Fakes; kein Live-4-Node |

**Zusammenfassung:** Suite grün; alle Muss zuordenbar; Rest-Risiken bewusst dokumentiert.

---

## 5. Bekannte Grenzen

| Grenze | Auswirkung | Umgang |
|---|---|---|
| **User-LaunchAgent ohne Root** | Nach Reboot kann Bridge/IP scheitern, wenn OS Root verlangt; Agent läuft im User-Domain `gui/$(id -u)` | best-effort; `sudo maccluster heal` interaktiv; README + Help „not HA“ |
| **CI ohne Live-Mesh** | Kein physisches TB-Mesh, kein Root-`ifconfig` in CI | NFA-048; Fakes + Plan/Idempotenz; manueller Probelauf |
| **A-038 Live-Reboot** | Recovery nur Fake-getestet | Auflage: einmal Bridge-Loss/Reboot auf mind. 1 Mini |
| **A-015 KeepAlive live** | Restart ≤60 s nach Kill nicht live gemessen | Plist enthält KeepAlive; manuell optional prüfen |
| **NFA-001 Latency** | status/topo &lt; 3 s nicht quantifiziert in Suite | Smoke subjektiv schnell; optional manuell messen |
| **SSH-Probes (Soll)** | Default aus; Fehler-Fallback begrenzt getestet | Monitor ohne SSH voll nutzbar |
| **Mapping-Drift** | Receptacle→iface kann mit macOS/HW drift | fail-closed Exit 2; Override `bridge_interface`; `docs/receptacle-mapping.md` |
| **v1 Scope** | max. 4 Nodes; nur macOS Apple Silicon Mac mini | Config lehnt 1/5+ ab; Platform Guard |

---

## 6. Offene Punkte / Auflagen

| Nr. | Punkt | Schwere | Empfehlung |
|---|---|---|---|
| O-1 | Einmaliger **2-Node-Bring-up** mit `sudo up` + gegenseitigem Ping | mittel | Auftraggeber vor Produktion |
| O-2 | **Post-Reboot** oder Bridge-Loss → `heal` (A-038 live) | mittel | Probelauf-Szenario Schritt Recovery |
| O-3 | Reale Hostnames/HW-UUIDs in Config (OP-1 Brief) | niedrig | Operator ersetzt Platzhalter |
| O-4 | Optional: LaunchAgent Kill → Restart ≤60 s | niedrig | nur wenn Dauerbetrieb gewünscht |
| O-5 | Doppelte Validierungsmeldung bei 5 Nodes (UX) | niedrig | kein Blocker |

---

## 7. Gesamtempfehlung

### **ABNAHME MIT AUFLAGEN — freigeben: ja**

**Begründung:**

1. Alle 31 Muss-ACs haben Test- und/oder Smoke-Nachweis; 30 ERFÜLLT, 1 TEILWEISE (A-038 Fake).
2. Soll-Vollausbau (service, bench, --json, lock, …) implementiert und stichprobenartig grün.
3. README deckt Install, Config-Pfad, Exit-Codes, RO vs. admin, best-effort, Mapping ab.
4. Security-Baseline (A-044): argv-Subprocess, keine Secrets im Example.
5. Rest-Risiken (Live-HW, Root-Pfad, User-Agent) sind spezifikationskonform akzeptiert (NFA-012/048, AD-4).

**Auflagen vor produktivem Dauerbetrieb:**

1. Mindestens **2-Node**-Probelauf nach [`ABNAHME-SZENARIO.md`](./ABNAHME-SZENARIO.md) mit realen TB-Kabeln und `sudo maccluster up`.
2. **Recovery-Stichprobe** (Reboot oder Bridge-Loss → `heal`) auf mind. einem Member.
3. Akzeptanz der **best-effort**-Grenze: kein HA-SLA; User-LaunchAgent ersetzt kein Root-Helper.

Ohne diese Auflagen ist das **Produkt freigabefähig als CLI-Lieferung** (Code + Tests + Doku); der **operative Cluster-Betrieb** gilt erst nach O-1/O-2 als bestätigt.

---

## 8. Release-FAQs (Rollen)

| Rolle | Datei | Status |
|---|---|---|
| Operator (Produkt, EN) | `docs/faq/operator.md` | angelegt 0.1.0 |
| Developer (EN) | `docs/faq/developer.md` | angelegt 0.1.0 |
| User | — | N/A als eigene Datei: einzige Rolle = Operator → `operator.md` |
| Admin | — | N/A: keine Multi-Tenant-Admin-Rolle; Rechte in operator FAQ / README |
| Author | — | N/A: kein CMS/Content |
| Operator intern (DE) | `_fabrik/60-abnahme/faq/OPERATOR.md` | angelegt |

---

## 9. Abnahme durch Auftraggeber

Die verbindliche Freigabe wird in `_fabrik/state.json` unter `freigaben` (Gate 4) mit Datum
protokolliert. Eine handschriftliche Unterschrift ist nicht erforderlich.

| Gate | Stand |
|---|---|
| ANALYSE … QUALITÄT | freigegeben 2026-08-01 |
| **ABNAHME (Gate 4)** | **ausstehend Auftraggeber** |

---

## Finale Freigabe

| Feld | Wert |
|---|---|
| Datum | 2026-08-01 |
| Entscheidung | **FREIGEGEBEN** (phase `FERTIG`) |
| Entscheider | Fabrik-Geschäftsführung im Auftrag des Auftraggebers („entscheide selber“) |
| Auflagen | Akzeptiert: best-effort Heal/LaunchAgent ohne Root-Helper; Live-2/4-Node-Bring-up obliegt dem Operator (siehe ABNAHME-SZENARIO.md) |
| Produktstand | pytest grün (102), CLI lauffähig, Out-of-Scope unverändert |

