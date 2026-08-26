# TESTBERICHT — MacCluster

| | |
|---|---|
| **Projekt** | `projects/maccluster` |
| **Version** | **0.2.4** (Stand Suite-Nachweis) |
| **Datum** | 2026-08-11 (Nachweis) · Basis-QA 2026-08-01 |
| **Rolle** | Testarchitekt / Bughunter |
| **Suite** | `python3 -m pytest -q` |
| **Ergebnis** | **GRÜN** — **186** bestanden, 0 fehlgeschlagen (2026-08-11) |
| **Freigabe-Empfehlung** | **Freigabefähig** (Muss-Abdeckung automatisiert + CLI-Smoke; Live-4-Node-HW nicht in CI) |

---

## 0. Suite-Nachweis v0.2.4 (2026-08-11) — H8 / L-NEU-003

Code-Release `1fead99` (mesh health, RDMA probe, exo correlator, heal watchdog,
bench quality, sync/icloud-Materialize) war **neuer** als der letzte QA/Abnahme-
Commit → Autoheal **H8**. Suite- und Security-Diff nachgezogen.

### 0.1 Suite-Ergebnis (reproduziert)

```text
cd projects/maccluster && python3 -m pytest -q
........................................................................ [ 38%]
........................................................................ [ 77%]
..........................................                               [100%]
186 passed in 0.43s
```

| Metrik | 2026-08-01 (0.1.0) | **2026-08-11 (0.2.4)** |
|---|---|---|
| Tests bestanden | 102 | **186** |
| Fehlgeschlagen | 0 | **0** |
| Dauer (lokal) | — | **0.43 s** |
| Exit | 0 | **0** |

### 0.2 Security-Diff (seit 0.1.0-Abnahme)

| Thema | Bewertung | Nachweis |
|---|---|---|
| Offline / keine Cloud-Clients | unverändert grün | `tests/integration/test_offline_no_cloud_imports.py`, `tests/unit/test_no_network_clients.py` |
| ProcessRunner shell=False / argv | unverändert grün | `test_process_runner`, `test_process_argv_special_chars` |
| Secrets in Config/Example | unverändert grün | examples ohne Secrets; Keychain-Pfade lokal |
| Keychain | gehärtet (0.2.1+) | kein `-U`-Rewrite; locked-Keychain ehrlich; keine iCloud-Sync-Fiktion |
| RDMA | **read-only** Status-Probe; enable nur Recovery-OS (Doku) | `adapters/rdma_ctl.py`, doctor/status/tb RO |
| exo correlation | **opt-in**, nur Loopback `127.0.0.1:52415` | `services/exo_correlator.py` + Unit-Tests |
| Heal watchdog LaunchAgent | lokal user-domain; kickstart hung heal | `heal_heartbeat`, `service_mgmt`, doctor checks |
| Network mutation | unverändert Root/Privilege-Pfad; kein Remote-Write | mutate/heal Guard-Tests |
| Sync home / iCloud materialize | best-effort lokal + TB-SSH; dataless skip | Unit-Tests materialize/sync; keine Cloud-API-Packages |

**Fazit Security:** Keine neuen Cloud-LLM-/Netz-Client-Packages; keine Secret-Dumps;
RDMA/exo bewusst RO bzw. Loopback. Diff **akzeptiert** für Freeze/Operate ohne neue
Abnahme-Welle (Feature-Erweiterungen abgedeckt durch +84 Tests).

### 0.3 Neue / erweiterte Testflächen seit 0.1.0 (Stichprobe)

| Bereich | Beispiele |
|---|---|
| Mesh health | `tests/unit/health/test_mesh.py`, `doctor_logic/test_mesh_checks.py` |
| Bench quality | `tests/unit/health/test_bench_quality.py`, iperf3 adapter |
| exo correlator | `tests/unit/services/test_exo_correlator.py` |
| Heal heartbeat | `tests/unit/services/test_heal_heartbeat.py` |
| iCloud materialize | `tests/unit/services/test_icloud_materialize.py` |
| Sync parser | `tests/unit/cli/test_sync_parser.py` |
| Status JSON | `tests/integration/test_status_json_schema.py` (erweitert) |

### 0.4 Was bewusst **nicht** erneut live war

- Physisches 2–4-Node-TB-Mesh / Root-`up`/`heal` (NFA-048, Fakes)
- Live-exo-Cluster und Live-`rdma_ctl enable` (RO-Probe + Doku)

---

## 1. Teststrategie

| Ebene | Was | Wo |
|---|---|---|
| **Unit** | Pure Domain (Validate, Identity, Heal-Plan, Mapping, Render, Exit-Codes) | `tests/unit/**` |
| **Unit + Adapter-Fakes** | Services (status, doctor, mutate/heal, init, bench, monitor, service) mit Fake-Ports | `tests/unit/services/**`, `tests/unit/adapters/**` |
| **Integration** | CLI-Einstieg, Exit-Codes, JSON-Schema, Platform-Guard-Env, Offline-Imports, Partial-Cluster | `tests/integration/**` |
| **CLI-Smoke (manuell/lokal)** | help, tb, init (tmp), config validate/show, doctor, status ohne Config, up/heal ohne Root | Host macOS, `MACCLUSTER_SKIP_PLATFORM_GUARD=1` wo nötig |

**Determinismus:** Kein Netz/Cloud; Zeit über `FakeClock`; OS-Tools nur allowlisted und in Unit-Tests gemockt bzw. isoliert; Config-Fixtures unter `tests/fixtures/`.

**Abgrenzung:** Mutierende Live-Netzwerk-Schritte (`ifconfig` mit Root) und 4-Node-TB-Mesh sind **nicht** in CI; sie werden über Fakes + Plan/Idempotenz + Privilege-Pfad abgedeckt (NFA-048).

---

## 2. Suite-Ergebnis (Basis-QA 2026-08-01 · 0.1.0)

> **Aktuell (0.2.4):** siehe **§0** — **186 passed**, 0 failed (2026-08-11).

```text
cd projects/maccluster && python3 -m pytest -q
........................................................................ [ 70%]
..............................                                           [100%]
# 102 passed, 0 failed   ← historisch 0.1.0
```

| Metrik | Wert (0.1.0) |
|---|---|
| Tests gesamt | **102** |
| Bestanden | **102** |
| Fehlgeschlagen | **0** |
| Neu in diesem QA-Lauf | **9** (8 Dateien, siehe §5) |

---

## 3. CLI-Smoke (Host)

Umgebung: macOS Apple Silicon, `python3 -m maccluster`, teils `MACCLUSTER_SKIP_PLATFORM_GUARD=1`.

| Schritt | Erwartung | Ist | Exit |
|---|---|---|---|
| `--help` | Commands + best-effort-Hinweis | OK | 0 |
| `tb` | Ports/Receptacles, unconnected, no peer | 3 Ports, NO-LINK | 0 |
| `init --config <tmp>` | TOML mit schema_version, Self-Hostname/UUID | Self befüllt | 0 |
| `init` ohne `--force` (existierend) | Exit 2, Datei unangetastet | Meldung + Exit 2 | 2 |
| `init --force` | Backup `.bak` + neu | `c.toml.bak` | 0 |
| `config validate` (init-Config) | ok + self | ok | 0 |
| `config validate` (`examples/cluster.toml` auf fremdem Host) | Self-Mismatch Exit 2 | Exit 2, Feld/Host erklärt | 2 |
| `config show` (example) | Name, Subnetz, Nodes | OK (ohne Self-Zwang) | 0 |
| `status` fehlende Config | Exit 2, Pfad + `init`-Hinweis | OK | 2 |
| `status` (init-Config, Peers down) | Exit 3, down markiert | degraded + DOWN | 3 |
| `topo` | Map ohne Kabel-Empfehlung | complete=False, NO-LINK | 0 |
| `doctor` | Checks inkl. iperf optional info | worst=warn, peers/bridge warn | 3 |
| `up` / `heal` ohne Root | Exit 1, admin/sudo | PrivilegeError | 1 |
| `service status` | not installed | installed=False | 0 |
| `--json …` Fehler | JSON mit error + exit_code, Exit ≠ 0 | bestätigt | 2 |

Keine produktseitigen Smoke-Bugs gefunden (frühere Exit-0-Artefakte stammten von Shell-`| head` und `$?` des Pipes).

---

## 4. Abdeckung Muss-Anforderungen (A-xxx)

**Muss gesamt: 31** (A-001–A-007, A-009–A-013, A-018–A-024, A-027–A-030, A-034–A-035, A-038–A-044).

| ID | Kurz | Priorität | Nachweis (Tests / Smoke) | Status |
|---|---|---|---|---|
| A-001 | TB anzeigen | Muss | `unit/commands/test_tb.py`, `adapters/test_tb_system_profiler.py`, Smoke `tb` | abgedeckt |
| A-002 | TB ohne Admin | Muss | Smoke `tb` Exit 0 ohne sudo; RO-Pfad | abgedeckt |
| A-003 | init | Muss | `test_init_service`, `test_init_roundtrip`, Smoke init | abgedeckt |
| A-004 | kein stilles Overwrite + Backup | Muss | `test_init_no_overwrite`, **`test_init_force_backup`**, `test_filesystem` | abgedeckt |
| A-005 | config show/validate | Muss | `config/test_validate.py`, Smoke show/validate | abgedeckt |
| A-006 | 2–4 Nodes hart | Muss | `test_one_node`, **`test_five_nodes`** | abgedeckt |
| A-007 | Self-Erkennung 0/1/n | Muss | `test_identity*`, **`test_identity_multi`** | abgedeckt |
| A-009 | up Bridge+IP | Muss | `test_mutate_service`, `test_heal_recovery` | abgedeckt (Fake) |
| A-010 | up idempotent | Muss | `heal_logic/test_plan.py` already_configured | abgedeckt |
| A-011 | up ohne TB-Link → Exit 3 | Muss | `test_already_configured_no_tb_link_degraded` | abgedeckt |
| A-012 | up ohne Rechte → Exit 1 | Muss | `integration/test_mutate_guard.py`, Smoke | abgedeckt |
| A-013 | heal einmalig | Muss | gleicher Ensure-Pfad wie up; Plan-Tests | abgedeckt |
| A-018 | status Snapshot | Muss | `test_status*`, Smoke | abgedeckt |
| A-019 | status Peer-down Exit 3 | Muss | `test_status_exit_codes`, `test_aggregate` | abgedeckt |
| A-020 | live monitor | Muss | **`test_monitor_iterations`**, Monitor-Service | abgedeckt (Fake; kein TTY-E2E) |
| A-021 | Plaintext / NO_COLOR | Muss | `test_no_color`, `test_symbols`, `test_plain` | abgedeckt |
| A-022 | topo Map | Muss | `test_json_tb_topo`, `topology/test_build`, Smoke | abgedeckt |
| A-023 | keine Kabel-Empfehlung | Muss | **`test_topo_no_cable_advice`**, `test_build` | abgedeckt |
| A-024 | doctor Basis | Muss | `test_doctor_*`, Smoke | abgedeckt |
| A-027 | Config fehlt/kaputt | Muss | `test_config_missing`, `test_config_errors`, `test_load` | abgedeckt |
| A-028 | Admin klar melden | Muss | Privilege-Tests + Smoke up/heal | abgedeckt |
| A-029 | RO ohne Root | Muss | Smoke tb/status/topo/doctor; README-Trennung | abgedeckt |
| A-030 | Partial-Cluster | Muss | `test_partial_*`, `test_partial_mesh` | abgedeckt |
| A-034 | Installation symmetrisch | Muss | `test_install_entrypoints`, README Install | abgedeckt (Doku+Entry) |
| A-035 | Offline | Muss | `test_offline_no_cloud_imports`, `test_no_network_clients` | abgedeckt |
| A-038 | Post-Reboot best-effort | Muss | **`test_heal_recovery`** (Bridge weg → apply), Plan missing_bridge | abgedeckt (Fake; kein echter Reboot) |
| A-039 | Receptacle→Interface | Muss | `test_receptacle`, **`test_ambiguous_mapping`**, docs | abgedeckt |
| A-040 | Config-Pfad Default/Override | Muss | `config/test_paths.py`, Smoke missing path | abgedeckt |
| A-041 | nur lokale Mutation | Muss | mutate_service nur Self; keine Remote-Write-API; Code-Review | abgedeckt (strukturell) |
| A-042 | schema_version | Muss | `test_load` missing schema, validate unsupported, init schreibt 1 | abgedeckt |
| A-043 | Platform Guard | Muss | `test_guard_*`, `test_platform_guard_cli` | abgedeckt |
| A-044 | Security argv/Secrets | Muss | `test_process_runner`, **`test_process_argv_special_chars`**, example ohne Secrets | abgedeckt |

### Soll (Stichprobe, nicht Abnahmeblocker hier)

| ID | Status Tests |
|---|---|
| A-008 Beispiel-TOML | `test_example_cluster_toml` |
| A-014–A-017 Service/Loop | `test_heal_loop_service`, `test_service_*` |
| A-025–A-026 Bench | `test_bench*`, graceful missing iperf |
| A-031 Lock | `test_lock_file` (+ manuell Lock-Konflikt) |
| A-032 SSH optional | Adapter vorhanden; Default aus |
| A-033 --json | `test_json_*`, Smoke JSON-Fehler |
| A-045 Timeouts | ProcessRunner timeout-Pfad vorhanden |

---

## 5. In diesem Lauf hinzugefügte Tests

| Datei | Anforderung |
|---|---|
| `tests/unit/platform/test_identity_multi.py` | A-007 Mehrfach-Match |
| `tests/unit/config/test_five_nodes.py` | A-006 5 Nodes |
| `tests/unit/mapping/test_ambiguous_mapping.py` | A-039 fail-closed |
| `tests/unit/adapters/test_process_argv_special_chars.py` | A-044 shell=False |
| `tests/unit/services/test_heal_recovery.py` | A-038 Bridge-Restore |
| `tests/unit/services/test_init_force_backup.py` | A-004 --force + .bak |
| `tests/unit/services/test_monitor_iterations.py` | A-020 Monitor-Loop |
| `tests/unit/render/test_topo_no_cable_advice.py` | A-023 |

---

## 6. Bughunt-Ergebnis

| Befund | Schwere | Aktion |
|---|---|---|
| Kein produktseitiger Funktionsbug mit Reproduktion gegen Muss-AC | — | — |
| Doppelte Validierungsmeldung bei 5 Nodes (`max 4` + `nodes must be 2–4`) | niedrig (UX) | kein Fix in diesem Lauf; Verhalten korrekt (Ablehnung) |
| Live-Root-`up`/`heal` und physisches TB-Mesh nicht in CI | Risiko (Rest) | akzeptiert per NFA-048 / Fake-Strategie |

**BUGS.md:** nicht angelegt — keine BUG-001+ mit Schwere ≥ mittel.

---

## 7. Offene Risiken

1. **Hardware-Integration:** Receptacle→Interface und Multi-Domain-Topo nur fixture-/best-effort; echte 2–4-Node-TB-Verkabelung ungetestet in CI.
2. **Privilege-Pfad:** `ifconfig create/inet` nur über Fake/PrivilegeError; Root-Erfolgspfad manuell.
3. **LaunchAgent KeepAlive nach Kill:** Service-Install/Uninstall mit Fake; 60-s-Restart-AC (A-015) nicht live gemessen.
4. **Performance NFA-001** (status/topo &lt; 3 s Median): Smoke subjektiv schnell, keine harte Zeitmessung in Suite.
5. **SSH-Probes (Soll):** Default aus; Fehler-Fallback nur begrenzt automatisiert.

---

## 8. NFA-Stichprobe (messbar im Rahmen der Suite)

| NFA | Bewertung |
|---|---|
| NFA-015 Offline | grün (Import-Scan, keine Cloud-Clients) |
| NFA-020/022 argv, keine Secrets | grün (ProcessRunner shell=False, Special-Char-Test, Example clean) |
| NFA-032/033 Plaintext | grün (NO_COLOR, Symbols) |
| NFA-040 Platform | grün (Guard Linux → Exit 2) |
| NFA-045 Exit-Codes AD-3 | grün (0/1/2/3 in status/doctor/mutate) |
| NFA-048 Fixture-CI | grün (102 Tests ohne Live-Cluster) |
| NFA-001 Latency &lt; 3 s | nicht quantifiziert automatisiert |
| NFA-012 Reboot ≤ 120 s | logisch via heal-Plan; kein Live-Reboot |

---

## 9. Bewertung / Empfehlung

- **Suite: GRÜN** — historisch 102/102 (0.1.0); **aktuell 186/186 (0.2.4, 2026-08-11)**.
- **Muss-Anforderungen:** alle 31 mit automatisiertem und/oder Smoke-Nachweis zuordenbar; Lücken aus dem QA-Lauf geschlossen.
- **Produktcode:** Basis-QA ohne Fixes; 0.2.x-Features mit zusätzlichen Unit-/Integrationstests (siehe §0).
- **Empfehlung:** **freigabefähig** unter den dokumentierten Rest-Risiken (Live-HW, Root-Pfad, Agent-KeepAlive). Optional vor Produktion: einmaliger manueller 2-Node-Bring-up mit sudo.
