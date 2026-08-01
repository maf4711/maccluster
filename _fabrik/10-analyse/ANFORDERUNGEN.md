# Anforderungen — MacCluster

| Feld | Wert |
|---|---|
| Projekt | MacCluster (`maccluster`) |
| Slug | maccluster |
| Phase | 1 ANALYSE |
| Quelle | `_fabrik/00-intake/BRIEF.md` (2026-08-01) |
| Stand | 2026-08-01 |
| Status | **Maßgebliche Spezifikation** (Lead-Analyst konsolidiert) |
| Projektmodus | Vollausbau (Muss + Soll in einem Durchlauf) |
| Gates | autopilot (Gate 4 Abnahme bleibt) |

Dieses Dokument ist die **verbindliche Anforderungsspezifikation**. Architektur und Planung
arbeiten ohne Rückfragen auf dieser Basis. Widersprüche zwischen Analyse-Artefakten sind hier
aufgelöst und als **ANNAHME** dokumentiert.

---

## Verwandte Artefakte

| Artefakt | Pfad | Inhalt |
|---|---|---|
| User Stories | [`USER-STORIES.md`](./USER-STORIES.md) | US-001–US-026, Akzeptanzkriterien, MoSCoW |
| Domänenmodell | [`DOMAENENMODELL.md`](./DOMAENENMODELL.md) | Entitäten, Invarianten, Glossar (DE + EN-Code) |
| NFA | [`NFA.md`](./NFA.md) | Nicht-funktionale Anforderungen NFA-001–NFA-049 |
| Risiken | [`RISIKEN.md`](./RISIKEN.md) | Risiken R-F/T/D/R/P mit Gegenmaßnahmen |
| Randfälle | [`RANDFAELLE.md`](./RANDFAELLE.md) | RF-Fx-nn, Exit-Codes, Fehlerpfade |

Traceability: Jede Anforderung verweist auf Stories (US-xxx) und ggf. NFA/RF. Stories decken
die A-IDs ab (Mapping in Abschnitt 5).

---

## 1. Ziel und Scope

### 1.1 Produktziel

**MacCluster** ist ein CLI-Werkzeug (`maccluster`) für bis zu vier Apple Silicon Mac minis,
die über Thunderbolt-Kabel dauerhaft als Netzwerk-Cluster betrieben werden. Das Produkt läuft
**symmetrisch** auf jedem Cluster-Member (kein dedizierter Leader).

**Nutzenversprechen:** Ein Befehlssatz, mit dem der Operator den Thunderbolt-Cluster
initialisiert, dauerhaft erreichbar hält und live im Terminal überwacht — auf jedem Member
identisch installierbar.

**Erfolgskriterium (ANNAHME Brief #1):** Innerhalb von 3 Monaten: 4 Nodes per Config erreichbar;
`monitor` zeigt korrekte TB-Links; Heal stellt Bridge/IP nach Reboot wieder her.

### 1.2 Primäre Rolle

| Rolle | Rechte |
|---|---|
| **Operator** (einzige Rolle) | Alle CLI-Befehle unter dem lokalen macOS-Benutzer; `up`/`heal`/`service install` dürfen Admin/sudo benötigen |

Keine App-Nutzerverwaltung, kein Login, kein Multi-User (Brief H, ANNAHME 3).

### 1.3 In Scope (v1 Vollausbau)

| ID | Funktion | MoSCoW |
|---|---|---|
| F1 | Thunderbolt-Hardware-Info (Ports, Fähigkeit, Link-Speed, Peers) | Muss |
| F2 | Cluster-Config (TOML, feste TB-IPs, Node-Identität Hostname/HW-UUID), `init` | Muss |
| F3 | Bring-up `up` (TB-Bridge + feste IP pro Node) | Muss |
| F4a | Heal einmalig | Muss |
| F4b | Heal-Loop + LaunchAgent service install/uninstall/status | Soll |
| F5 | Live-CLI-Monitor + Status-Snapshot | Muss |
| F6 | Topologie-Map (Auto-Detect Domain-UUID / Kabel-Map) | Muss |
| F7a | Doctor/Diagnose (Basis) | Muss |
| F7b | Bandwidth-Bench wenn `iperf3` vorhanden | Soll |
| — | Optionales JSON-Output (`--json`) | Soll |
| — | Optionale SSH-Peer-Probes | Soll |
| — | Erweiterte Historie / Log-Rotation | Kann |
| — | Farbige Rich-TUI (optional Dependency) | Kann |

**Leuchtturm:** Live-Monitor + Auto-Detect-Topologie auf jedem Member.

### 1.4 Explizit Out-of-Scope

Wird **nicht** gebaut (Brief L-05 bestätigt):

- Grafische Oberfläche / Web-UI / Desktop-App
- Öffentliche HTTP-API für Dritte
- Cloud-Deploy, Docker-Pflicht, Multi-Tenant
- Linux/Windows als Zielplattform (nur macOS Apple Silicon Mac mini)
- exo / LLM-Inference-Orchestrierung / RDMA-Enablement (Recovery-OS)
- Zentrale Datenbank, Multi-User-Login, OAuth
- Automatische physische Kabelführungs-Empfehlung jenseits von `topo`
- Live-Trading-Guards und Fremdprodukt-Anbindungen (Neutralität der Fabrik)
- HA-/SLA-Garantie jenseits Best-effort Heal + LaunchAgent-Restart
- Cluster > 4 Nodes

### 1.5 Plattform & technische Rahmenbedingungen

| Aspekt | Vorgabe |
|---|---|
| Plattform | macOS, Apple Silicon Mac mini, Thunderbolt/USB4 |
| Stack | Python 3.11+, stdlib primär; optional `rich` für Monitor |
| Persistenz | Lokale Dateien (TOML-Config, optional Logs); **keine DB** |
| Offline | Keine Cloud-Abhängigkeit; rein lokal |
| Symmetrie | Dieselbe Installation und Config-Struktur auf jedem Member |
| Rechte | Read-only ohne Root; `up`/`heal`/`service install` dürfen Admin brauchen und melden das klar |
| CLI-Sprache | Englisch (Messages, Help, README); Fabrik-Artefakte Deutsch |
| Auslieferung | `pipx install` / `pip install -e .` / `install.sh`; LaunchAgent via `service install` |

### 1.6 Externe Integrationen (lokal, keine Drittsystem-APIs)

| System | Zweck | Richtung |
|---|---|---|
| `system_profiler` / `ioreg` | TB-Hardware | lesen |
| `ifconfig` / `networksetup` | Bridge, IPs, Interfaces | lesen/schreiben |
| `ping` | Peer-Erreichbarkeit | lokal |
| `launchctl` | Heal-Service | lokal |
| `iperf3` (optional) | Bandwidth-Bench | lokal, wenn installiert |
| SSH (optional, Config) | Peer-Probe remote | ausgehend, nur wenn Keys + Flag |

---

## 2. Verbindliche Architektur-Defaults (Widersprüche gelöst)

Diese Defaults schließen die offenen Brief-Punkte und Widersprüche zwischen Analysten.
Sie sind für Architektur und Implementierung **verbindlich**, bis Gate 4 sie ändert.

| ID | Thema | Entscheidung | Begründung |
|---|---|---|---|
| **AD-1** | Subnetz-Default | `10.42.0.0/24` | Brief-Vorschlag OP-2; privat, selten kollidierend mit Heim-LAN; Override in Config |
| **AD-2** | SSH-Probes | **Optional**; Default **aus**; nur wenn Config-Flag **und** Keys vorhanden | Brief OP-3; Monitor muss ohne SSH voll nutzbar sein (R-A01, S-3, DM-3) |
| **AD-3** | Exit-Codes | `0` = ok · `1` = error (Laufzeit/System/Rechte) · `2` = usage (Args/Config-Validierung) · `3` = degraded (Teil-Erfolg / Cluster unvollständig) | Skriptierbar; löst Widerspruch RF-A0 vs. NFA-A17 vs. Story S-2 |
| **AD-4** | LaunchAgent | **User-Domain** `gui/$(id -u)` → `~/Library/LaunchAgents` | Weniger Root; RF-A14; Privilege-Elevation nur wo OS es verlangt |
| **AD-5** | `up` ohne aktiven TB-Link | Exit **3** (degraded) mit klarer Meldung „no TB link“; **IP trotzdem setzen**, wenn Bridge ok | Lokaler Bring-up vor Kabel; Operator sieht degraded, nicht false success (RF-F3-07 geschärft) |
| **AD-6** | Config-Pfad | Default `~/.config/maccluster/cluster.toml`; Override: CLI `--config` > Env `MACCLUSTER_CONFIG` > Default | A-027/A-040 prüfbar; XDG-ähnlich, portabel pro User |

### 2.1 Exit-Code-Semantik (verbindlich)

| Code | Bedeutung | Typische Auslöser |
|---|---|---|
| **0** | Erfolg / healthy | Operation ok; Info-Befehle mit lesbarer Ausgabe; Monitor sauber beendet (Ctrl+C) |
| **1** | Error | OS-Befehl fehlgeschlagen, fehlende Rechte, Hardware-Probe-Crash, Runtime-Fehler |
| **2** | Usage | Ungültige CLI-Args, fehlende/ungültige Config, Validierungsfehler, unsupported platform für Mutation |
| **3** | Degraded | Mind. ein Peer unreachable; `up` ohne TB-Link aber Bridge/IP gesetzt; cluster partial; doctor mit warn-only-kritisch je Doku |

**ANNAHME A-X1:** Bei `status`: alle Peers erreichbar → 0; ≥1 Peer down, Self ok → 3; Config/Probe-Hardfail → 2 bzw. 1. Monitor bleibt bei Peer-down laufend (Exit erst beim Beenden: 0 bei sauberem Abbruch).

**ANNAHME A-X2:** `doctor`: worst check `error` → Exit **1**; ≥1 `warn` zu Cluster-Erreichbarkeit/Bridge/Self (ohne error) → Exit **3**; nur Info-Warnungen (z. B. fehlendes iperf3, optionale Features) bei sonst ok → Exit **0**.

### 2.2 CLI-Unterbefehle (Arbeitsbezeichner)

**ANNAHME A-X3:** Sinngemäße Befehle (Architektur darf Aliase wählen, Semantik bleibt):

`tb` · `init` · `config show|validate` · `up` · `heal` [`--loop`] · `status` · `monitor` · `topo` · `doctor` · `bench` · `service install|uninstall|status`

---

## 3. Funktionale Anforderungen

Konvention: Jede Anforderung hat **genau ein prüfbares Abnahmekriterium**.  
Priorität: **Muss** | **Soll** | **Kann**.

### 3.1 Thunderbolt-Hardware (F1)

#### A-001 — TB-Ports und Link-Info anzeigen
| | |
|---|---|
| **Priorität** | Muss |
| **Story** | US-001 |
| **Beschreibung** | Das CLI zeigt Thunderbolt-/USB4-Hardware je Port/Receptacle: Identität, Fähigkeit/Version, Interface-Zuordnung (soweit ermittelbar), verhandelte Link-Geschwindigkeit bzw. „nicht verbunden“, angeschlossene Peers/Domain-Hinweise. |
| **Abnahmekriterium** | `maccluster tb` listet auf einem Apple-Silicon-Mac-mini mit sichtbarem TB-Port je Port mindestens Port-ID, Fähigkeit, Link-Speed oder unconnected; ohne Peer klar „no peer“/unconnected (Text+Symbol, nicht nur Farbe). Exit 0 bei lesbarer Hardware. |

#### A-002 — TB-Info ohne Admin-Rechte
| | |
|---|---|
| **Priorität** | Muss |
| **Story** | US-001, US-012 |
| **Beschreibung** | Hardware-Info läuft als normaler macOS-Benutzer ohne Privilege-Elevation. |
| **Abnahmekriterium** | `maccluster tb` ohne sudo liefert lesbare Daten oder klare Diagnose bei OS-Lücken; kein interaktiver sudo-Prompt. |

---

### 3.2 Cluster-Config (F2)

#### A-003 — Cluster initialisieren (`init`)
| | |
|---|---|
| **Priorität** | Muss |
| **Story** | US-002 |
| **Beschreibung** | `init` erzeugt eine gültige TOML-Config-Vorlage mit Cluster-Name, Subnetz (Default `10.42.0.0/24`), Interface und 2–4 Node-Stubs (id, hostname, ip, hw_uuid-Platzhalter). Self-Node wird soweit möglich vorausgefüllt. |
| **Abnahmekriterium** | Nach `maccluster init` existiert eine parsebare TOML mit Name, Subnetz `10.42.0.0/24` (oder explizit gesetzt), Interface und ≥2 Node-Einträgen; lokaler Hostname und/oder HW-UUID des Hosts ist im Self-Stub gesetzt. |

#### A-004 — Kein stilles Config-Überschreiben
| | |
|---|---|
| **Priorität** | Muss |
| **Story** | US-002, US-010 |
| **Beschreibung** | Existierende Config wird ohne explizites Force nicht überschrieben. |
| **Abnahmekriterium** | `init` bei vorhandener Config ohne `--force`: Exit 2, Datei unverändert; mit `--force`: bestehende Datei vor Replace als Backup neben der Originaldatei (Suffix `.bak` oder Timestamp-`.bak`) gesichert, danach neue Config geschrieben. |

#### A-005 — Config anzeigen und validieren
| | |
|---|---|
| **Priorität** | Muss |
| **Story** | US-003 |
| **Beschreibung** | Config ist anzeigbar und vor mutierenden Ops validierbar (IPs, Subnetz, Eindeutigkeit, Node-Anzahl, Self-Match). |
| **Abnahmekriterium** | `config show` (o. ä.) zeigt Name, Subnetz, Interface, alle Nodes; `config validate` bzw. Laden bei `up`/`heal` bei doppelter IP/ungültigem Subnetz/fehlendem Feld: Exit 2, Feld benannt, keine Netzänderung. |

#### A-006 — Node-Anzahl 2–4 (hart)
| | |
|---|---|
| **Priorität** | Muss |
| **Story** | US-003 |
| **NFA** | NFA-008 |
| **Beschreibung** | v1 unterstützt genau 2–4 Nodes; 1 oder >4 werden abgelehnt. |
| **Abnahmekriterium** | Config mit 1 oder 5 Nodes → Exit 2, Meldung „2–4 nodes“ / „max 4 nodes in v1“; kein stilles Truncating. |

#### A-007 — Self-Node-Erkennung
| | |
|---|---|
| **Priorität** | Muss |
| **Story** | US-003 |
| **Beschreibung** | Genau ein Node wird als `self` erkannt (Match Hostname und/oder HW-UUID). |
| **Abnahmekriterium** | Bei keinem oder mehreren Matches: Exit 2 mit erklärender Meldung (was verglichen wurde); bei genau einem Match: `role=self` für diesen Node. |

#### A-008 — Config als portable TOML-Wahrheit
| | |
|---|---|
| **Priorität** | Soll |
| **Story** | US-026 |
| **Beschreibung** | Dieselbe TOML-Datei ist auf allen Members ablegbar; menschenlesbar, kein Binärformat. Beispiel-`cluster.toml` für bis zu 4 Nodes im Lieferumfang. |
| **Abnahmekriterium** | Gültige Config von Node A unverändert auf Node B geladen → gleiche Cluster-Definition, Self relativ zu Host; Beispiel-Datei im Repo dokumentiert. |

---

### 3.3 Bring-up (F3)

#### A-009 — Bridge und feste IP setzen (`up`)
| | |
|---|---|
| **Priorität** | Muss |
| **Story** | US-004 |
| **Beschreibung** | `up` setzt/bestätigt Thunderbolt-Bridge und feste Self-Node-IP gemäß Config. Nur lokaler Self-Node und nur TB-zugeordnete Interfaces/Bridges werden mutiert (kein Anfassen von Wi-Fi/Ethernet-Default-Route). |
| **Abnahmekriterium** | Bei gültiger Config und Rechten: Bridge + Self-IP gesetzt; CLI meldet Interface und IP; Exit 0 wenn Link up und alles ok, Exit 3 wenn Bridge/IP ok aber kein TB-Link (A-011); nicht-TB-Interfaces (z. B. en0 Wi-Fi) unverändert (Vergleich vor/nach). |

#### A-010 — Idempotenz von `up`
| | |
|---|---|
| **Priorität** | Muss |
| **Story** | US-004 |
| **NFA** | NFA-016 |
| **Beschreibung** | Erneutes `up` bei korrektem Zustand ist sicher. |
| **Abnahmekriterium** | Zweites `up` bei korrekter Bridge/IP: Exit 0, keine destruktive Rekonfiguration, Meldung „already configured“ o. ä. erlaubt. |

#### A-011 — `up` ohne aktiven TB-Link (degraded)
| | |
|---|---|
| **Priorität** | Muss |
| **Story** | US-004, US-020 |
| **Randfall** | RF-F3-07 |
| **Beschreibung** | Fehlt der physische TB-Link, setzt `up` dennoch Bridge/IP wenn möglich und signalisiert degraded. |
| **Abnahmekriterium** | Kabel ab, Bridge konfigurierbar: IP/Bridge gesetzt, Exit **3**, Meldung enthält „no TB link“ (o. ä.); Peer-Erreichbarkeit wird nicht als ok behauptet. |

#### A-012 — `up` ohne Admin-Rechte
| | |
|---|---|
| **Priorität** | Muss |
| **Story** | US-004, US-011 |
| **Beschreibung** | Fehlende Rechte führen zu klarem Abbruch, keinem stillen Partial-Success. |
| **Abnahmekriterium** | `up` ohne ausreichende Rechte: Exit 1, Meldung „admin/sudo required“; wenn Teilschritte liefen → dokumentierter Partial-State, nicht Exit 0. |

---

### 3.4 Heal (F4)

#### A-013 — Heal einmalig
| | |
|---|---|
| **Priorität** | Muss |
| **Story** | US-005 |
| **Beschreibung** | Einmaliges `heal` gleicht Bridge/IP gegen Config ab und korrigiert Drift (Bridge fehlt, Interface down, IP abweichend). |
| **Abnahmekriterium** | Bei Drift und Rechten: Korrektur angewendet, Ergebnis geheilt/ok/fehlgeschlagen ausgegeben; bei bereits healthy: Exit 0, keine unnötigen Resets. |

#### A-014 — Heal-Loop
| | |
|---|---|
| **Priorität** | Soll |
| **Story** | US-016 |
| **NFA** | NFA-003 |
| **Beschreibung** | `heal --loop` wiederholt Heal im konfigurierbaren Intervall (Default 30 s, Minimum 5 s). Best-effort, kein HA-SLA. |
| **Abnahmekriterium** | Loop läuft mit Default 30 s; Ctrl+C beendet sauber; Hilfe/README nennen „best-effort“, kein HA-Versprechen. |

#### A-015 — Service install (LaunchAgent)
| | |
|---|---|
| **Priorität** | Soll |
| **Story** | US-013 |
| **Beschreibung** | `service install` richtet einen User-Domain-LaunchAgent ein, der Heal im Loop ausführt (Domain `gui/$(id -u)`, Plist unter `~/Library/LaunchAgents`). Agent wird bei Prozessabsturz neu gestartet (KeepAlive o. ä.). |
| **NFA** | NFA-013 |
| **Abnahmekriterium** | Nach erfolgreichem Install: Agent registriert; `service status` zeigt installed; Intervall Default 30 s oder Config; Plist enthält KeepAlive (oder äquivalent); nach Kill des heal-Prozesses erscheint er innerhalb 60 s erneut (sofern Agent geladen); idempotent bei erneutem Install (Exit 0). |

#### A-016 — Service uninstall
| | |
|---|---|
| **Priorität** | Soll |
| **Story** | US-014 |
| **Beschreibung** | `service uninstall` entfernt/entlädt den LaunchAgent vollständig. |
| **Abnahmekriterium** | Nach Uninstall: `service status` = not installed; kein aktiver Heal-Loop des Tools; Uninstall ohne Installation: Exit 0 (idempotent). |

#### A-017 — Service status
| | |
|---|---|
| **Priorität** | Soll |
| **Story** | US-015 |
| **Beschreibung** | Status des Heal-Services abfragbar ohne Root (soweit User-Agent). |
| **Abnahmekriterium** | Ausgabe enthält mindestens: installed ja/nein, running ja/nein (soweit ermittelbar), Label/Pfad und Intervall. |

---

### 3.5 Status & Monitor (F5)

#### A-018 — Status-Snapshot
| | |
|---|---|
| **Priorität** | Muss |
| **Story** | US-006 |
| **NFA** | NFA-001 |
| **Beschreibung** | Einmaliger Cluster-Status: Nodes, konfigurierte IPs, Erreichbarkeit, Zeitstempel; Self gekennzeichnet. |
| **Abnahmekriterium** | `status` listet alle Config-Nodes mit id/hostname, IP, reachability (up/down/unknown), Timestamp; Median-Wandzeit < 3 s bei ≤4 Nodes; ohne Root lauffähig. |

#### A-019 — Status Exit bei Peer-down
| | |
|---|---|
| **Priorität** | Muss |
| **Story** | US-020 |
| **Beschreibung** | Teilausfälle sind vom Tool-Crash unterscheidbar und skriptierbar. |
| **Abnahmekriterium** | Self ok, ≥1 Peer unreachable: Exit **3**; Ausgabe markiert down-Nodes; unhandled Exception verboten. |

#### A-020 — Live-Monitor
| | |
|---|---|
| **Priorität** | Muss |
| **Story** | US-007 |
| **NFA** | NFA-002 |
| **Beschreibung** | Periodisch aktualisierender Terminal-Monitor (Default-Refresh 1–2 s) mit Nodes, TB-Link-Zustand (soweit lokal ermittelbar: connected/speed oder unconnected), Erreichbarkeit. |
| **Abnahmekriterium** | `monitor` aktualisiert periodisch; zeigt je Node Erreichbarkeit und TB-Link-Hinweis (nicht nur Ping); Zustandswechsel peer up→down sichtbar (Text/Symbol); Ctrl+C beendet sauber Exit 0; bei allen Peers down: Monitor läuft weiter, zeigt down. |

#### A-021 — Monitor ohne reine Farbabhängigkeit / Plaintext
| | |
|---|---|
| **Priorität** | Muss |
| **Story** | US-007, US-023 |
| **NFA** | NFA-032, NFA-033 |
| **Beschreibung** | Kritische Zustände ohne Farbe erkennbar; ohne `rich` voll nutzbar. |
| **Abnahmekriterium** | `NO_COLOR=1` bzw. ohne `rich`: alle Zustände in status/monitor unterscheidbar; Kernfunktion vollständig. |

---

### 3.6 Topologie (F6)

#### A-022 — Topologie-Map ausgeben
| | |
|---|---|
| **Priorität** | Muss |
| **Story** | US-008 |
| **NFA** | NFA-001 |
| **Beschreibung** | `topo` zeigt erkannte Links (Domain-UUID soweit verfügbar, Ports/Receptacles, Peer-Bezug) und gleicht mit Config ab. |
| **Abnahmekriterium** | Ausgabe listet Links/Karte; gematchte Nodes mit Config-id/hostname; unmatched ausgewiesen; ohne sudo; Median < 3 s. |

#### A-023 — Keine Kabelführungs-Empfehlung
| | |
|---|---|
| **Priorität** | Muss |
| **Story** | US-008 |
| **Beschreibung** | Out-of-Scope: keine automatische physische Umverkabelungs-Empfehlung. |
| **Abnahmekriterium** | `topo`-Ausgabe enthält keine „plug cable from X to Y“-Empfehlung jenseits der erkannten Map. |

---

### 3.7 Doctor & Bench (F7)

#### A-024 — Doctor Basisdiagnose
| | |
|---|---|
| **Priorität** | Muss |
| **Story** | US-009 |
| **NFA** | NFA-004 |
| **Beschreibung** | Gebündelte Checks: Config, Self-Node, TB-Ports, Bridge/Interface, Peer-Ping; je Check ok/warn/fail. |
| **Abnahmekriterium** | `doctor` führt ≥ die genannten Checks aus; kritischer Fail → Exit ≠ 0; ohne Root laufen lesende Checks; admin-only als skipped/needs admin markiert. |

#### A-025 — Bandwidth-Bench mit iperf3
| | |
|---|---|
| **Priorität** | Soll |
| **Story** | US-018 |
| **Beschreibung** | Optionaler Bench zu Peer-IP wenn `iperf3` im PATH und Peer erreichbar. |
| **Abnahmekriterium** | Bei vorhandenem iperf3 und erreichbarem Ziel: Durchsatz ausgegeben, Exit 0; ohne Ziel-Arg: Usage Exit 2. |

#### A-026 — Bench ohne iperf3 graceful
| | |
|---|---|
| **Priorität** | Soll |
| **Story** | US-019 |
| **Beschreibung** | Fehlendes iperf3 bricht nur Bench, nicht das restliche CLI. |
| **Abnahmekriterium** | `bench` ohne iperf3: Exit **1**, Meldung „iperf3 not found“ + Install-Hinweis; `doctor` markiert optional skip/warn, nicht cluster-fail (Exit nicht allein wegen fehlendem iperf3 auf 1). |

---

### 3.8 Fehlerbehandlung, Rechte, Robustheit

#### A-027 — Fehlende / kaputte Config
| | |
|---|---|
| **Priorität** | Muss |
| **Story** | US-010 |
| **Beschreibung** | Config-abhängige Befehle scheitern klar bei fehlender, unlesbarer oder syntaktisch ungültiger Config. |
| **Abnahmekriterium** | Keine Config: Exit 2, erwarteter Pfad + Hinweis `init`; TOML-Syntaxfehler: Exit 2, Datei/Position; Permission: Exit 1; keine Netzänderung. |

#### A-028 — Admin-Bedarf klar melden
| | |
|---|---|
| **Priorität** | Muss |
| **Story** | US-011 |
| **NFA** | NFA-019 |
| **Beschreibung** | Schreibende Ops melden Privilege-Bedarf; Read-only fordern kein sudo. |
| **Abnahmekriterium** | `up`/`heal` (korrigierend)/`service install` ohne Rechte: klare Meldung + Exit 1; `tb`/`status`/`monitor`/`topo`/lesender `doctor`: kein sudo-Prompt. |

#### A-029 — Read-only ohne Root
| | |
|---|---|
| **Priorität** | Muss |
| **Story** | US-012 |
| **Beschreibung** | Alltag-Beobachtung ohne elevatete Shell. |
| **Abnahmekriterium** | Als Normaluser: `tb`, `status`, `monitor`, `topo`, lesender `doctor` laufen ohne Root; README trennt read-only vs. admin-Befehle. |

#### A-030 — Peer-down und Partial-Cluster robust
| | |
|---|---|
| **Priorität** | Muss |
| **Story** | US-020 |
| **NFA** | NFA-014 |
| **Beschreibung** | Unerreichbare Peers, fehlende Kabel, 2-von-4 online stürzen das Tool nicht ab. |
| **Abnahmekriterium** | status/monitor/topo bei partial mesh: unterscheidbare up/down; Exit gemäß AD-3; kein Crash. |

#### A-031 — Mutierende Ops serialisieren (Lock)
| | |
|---|---|
| **Priorität** | Soll |
| **Story** | — (NFA-009, RF-F3-10) |
| **NFA** | NFA-009 |
| **Beschreibung** | Parallele `up`/`heal` auf einem Host erzeugen keinen korrupten Interface-State. |
| **Abnahmekriterium** | Zwei gleichzeitige mutierende Befehle: einer führt aus, der andere wartet oder Exit 1 mit „in progress“; nach Abschluss konsistenter Netz-State. |

---

### 3.9 SSH, JSON, Installation, Offline

#### A-032 — SSH-Probes optional
| | |
|---|---|
| **Priorität** | Soll |
| **Story** | US-021 |
| **NFA** | NFA-023 |
| **Beschreibung** | SSH-Remote-Probes nur bei Config-Flag und vorhandenen Keys; Default aus. BatchMode, Timeout (Default 3 s), kein Password-Prompt-Hang. |
| **Abnahmekriterium** | Ohne SSH: status/monitor/doctor voll nutzbar nur mit lokalen Probes, kein Hard-Error; mit SSH und Key: Zusatzinfos oder doctor-Anreicherung; SSH-Fehler → Fallback lokal + Warnung. |

#### A-033 — Optionales JSON-Output
| | |
|---|---|
| **Priorität** | Soll |
| **Story** | US-017 |
| **NFA** | NFA-046 |
| **Beschreibung** | `--json` für mindestens `status`, `tb`, `topo`, `doctor`; stabile Felder inkl. `schema_version`; keine öffentliche HTTP-API. |
| **Abnahmekriterium** | `--json` → parsebares JSON auf stdout mit Feld `schema_version`; mind. Nodes (id, ip, reachability) + Timestamp bei status; Fehler: valides JSON-Objekt mit Fehlerinfo + Exit ≠ 0; kein HTTP-Server. |

#### A-034 — Symmetrische Installation
| | |
|---|---|
| **Priorität** | Muss |
| **Story** | US-024 |
| **NFA** | NFA-042, NFA-043 |
| **Beschreibung** | Identische Installation auf jedem Member; ein Package; Plattformgrenze dokumentiert. |
| **Abnahmekriterium** | README: mindestens ein Install-Weg führt zu `maccluster` im PATH; zwei Nodes mit gleicher Version/Config-Struktur zeigen konsistente Self/Peer-Rollen; nur macOS Apple Silicon Mac mini als Ziel genannt. |

#### A-035 — Offline-Betrieb
| | |
|---|---|
| **Priorität** | Muss |
| **Story** | US-025 |
| **NFA** | NFA-015 |
| **Beschreibung** | Kernfunktionen ohne Internet und ohne Cloud-Account. |
| **Abnahmekriterium** | Bei deaktiviertem WAN: `init`, `tb`, `status`, `topo`, `doctor`, `up`, `heal` funktionieren mit lokalen OS-Tools; kein App-Login/OAuth. |

---


### 3.10 Lücken-Nachzug (Brief-Abgleich 2026-08-01)

#### A-038 — Post-Reboot-Wiederherstellung (Best-effort)
| | |
|---|---|
| **Priorität** | Muss |
| **Story** | US-005, US-013, US-016 |
| **NFA** | NFA-012 |
| **Beschreibung** | Erfolgskriterium Brief ANNAHME 1: Nach Reboot (oder äquivalentem Verlust von Bridge/IP) stellt `heal` bzw. der installierte Heal-Service Bridge und feste Self-IP gemäß Config best-effort wieder her. Kein quantifiziertes HA-/Uptime-SLA. |
| **Abnahmekriterium** | Szenario: gültige Config, zuvor korrekter Bring-up; Bridge/IP entfernt (Reboot oder manuelle Entfernung); danach einmaliges `heal` mit Rechten **oder** ein Tick des geladenen LaunchAgents → Bridge+Self-IP matchen Config innerhalb 120 s nach Heal-Start/Agent-Start; Exit 0 bei Erfolg; README/Help nennen „best-effort“, kein 99,x %-Versprechen. |

#### A-039 — Receptacle→Interface-Mapping testbar und dokumentiert
| | |
|---|---|
| **Priorität** | Muss |
| **Story** | US-001, US-004, US-008 |
| **Randfall** | RF-F3-08 |
| **Beschreibung** | Brief G: Mapping physischer TB-Ports/Receptacles zu Netzwerk-Interfaces auf Apple Silicon Mac mini ist als isolierte, fixture-testbare Logik umgesetzt und dokumentiert. Bei Ambiguität kein stilles Raten. |
| **Abnahmekriterium** | (1) Unit-/Fixture-Tests decken Mapping-Parser mit mind. einem Mac-mini-Sample ab (CI ohne Live-HW). (2) `tb` und/oder `doctor` zeigen raw-Port und gemapptes Interface. (3) Config-Override des Interface-Namens greift, wenn gesetzt. (4) Bei unklarem Mapping: mutierende Ops (`up`/`heal`) Exit **2** mit Diagnosehinweis (`tb`/`doctor`), kein stilles Falsch-Interface. (5) README dokumentiert bekannte Mini-Layouts bzw. Override. |

#### A-040 — Config-Pfad Default und Override
| | |
|---|---|
| **Priorität** | Muss |
| **Story** | US-002, US-010, US-026 |
| **NFA** | NFA-030 |
| **Beschreibung** | Persistenter Default-Pfad der Cluster-Config ist verbindlich festgelegt und überschreibbar; fehlende Config nennt den erwarteten Pfad (A-027). |
| **Abnahmekriterium** | Default-Pfad = `~/.config/maccluster/cluster.toml` (AD-6); Override per CLI `--config <path>` und optional Env `MACCLUSTER_CONFIG` (Env hat Vorrang vor Default, CLI hat Vorrang vor Env); `init` ohne Override schreibt Default-Pfad; fehlende Config: Exit 2 nennt den aufgelösten erwarteten Pfad; README dokumentiert alle drei Quellen. |

#### A-041 — Mutierende Ops nur lokal (kein Remote-Write)
| | |
|---|---|
| **Priorität** | Muss |
| **Story** | US-004, US-005 |
| **Beschreibung** | `up` und `heal` ändern ausschließlich den lokalen Host (Self-Node Bridge/IP/Service). Keine schreibenden Remote-Aktionen auf Peers (kein SSH-Write, kein Remote-ifconfig). |
| **Abnahmekriterium** | Bei Ausführung von `up`/`heal` auf Node A: nur A hat Netzänderungen; Peers unverändert; Code/Doku verbieten Remote-Mutation; optional SSH dient nur lesenden Probes (A-032). |

#### A-042 — schema_version in Cluster-Config
| | |
|---|---|
| **Priorität** | Muss |
| **Story** | US-002, US-003 |
| **Beschreibung** | Jede gültige Cluster-Config enthält `schema_version` (Ganzzahl ≥ 1). Unbekannte/fehlende Version → Validierungsfehler. |
| **Abnahmekriterium** | `init` schreibt `schema_version = 1` (oder aktuelle v1-Version); Config ohne Feld oder mit unsupported Version → Exit 2, Feld benannt; `config validate`/`up`/`heal` lehnen ab ohne Netzänderung. |

#### A-043 — Unsupported Platform Guard
| | |
|---|---|
| **Priorität** | Muss |
| **Story** | US-024 |
| **NFA** | NFA-040 |
| **Beschreibung** | Mutierende und plattformkritische Befehle auf nicht unterstützter Plattform (nicht macOS Apple Silicon) werden abgelehnt; Read-only dürfen best-effort warnen. |
| **Abnahmekriterium** | Auf unsupported OS/Arch: `up`/`heal`/`service install` → Exit **2**, Meldung nennt supported platform (macOS Apple Silicon Mac mini); `tb`/`status` o. ä. entweder Exit 2 oder Exit 0 mit deutlicher Warnung „unsupported“ (nicht still als supported behaupten). |

#### A-044 — Security-Baseline CLI (Secrets, argv-Subprocess)
| | |
|---|---|
| **Priorität** | Muss |
| **Story** | US-011, US-021 |
| **NFA** | NFA-020, NFA-022 |
| **Beschreibung** | Keine Secrets im Repo/Beispielen; OS-Tool-Aufrufe argument-separiert (kein Shell-String mit Config-/CLI-Werten); SSH speichert keine Passwörter. |
| **Abnahmekriterium** | Beispiel-`cluster.toml` und Repo ohne Key-Material/Passwörter; Subprocess-Aufrufe zu system_profiler/ifconfig/networksetup/ping/launchctl/iperf3/ssh ohne `shell=True`/unsichere Konkatenation (Code-Review + mind. ein Test mit Sonderzeichen in Hostname/Pfad); Private-Key-Inhalte erscheinen nie in Log/JSON/stdout. |

#### A-045 — Subprocess-Timeouts (Probes)
| | |
|---|---|
| **Priorität** | Soll |
| **Story** | US-006, US-009, US-018, US-021 |
| **Beschreibung** | Externe Probes (`ping`, optional SSH, optional iperf3) haben harte Timeouts; kein unbegrenztes Hängen der CLI. |
| **Abnahmekriterium** | Ping-Probe Default-Timeout ≤ 2 s pro Peer; SSH Default 3 s (A-032); iperf3-Bench Default-Testdauer ≤ 5 s/Peer und Gesamt-Timeout greift; bei Timeout: Peer/Check als down/fail markiert, Prozess endet, kein Hang > Timeout+1 s. |

---

### 3.11 Kann-Anforderungen


#### A-036 — Optionales Action-Log mit Rotation
| | |
|---|---|
| **Priorität** | Kann |
| **Story** | US-022 |
| **NFA** | NFA-010, NFA-024 |
| **Beschreibung** | Optionales Append-Log für up/heal (Default aus); Rotation begrenzt Wachstum (Default max 5 MiB). |
| **Abnahmekriterium** | Default: kein ausführliches Action-Log; bei Aktivierung: Timestamp+Aktion; Rotation greift vor unbegrenztem Wachstum. |

#### A-037 — Optionale Rich-TUI
| | |
|---|---|
| **Priorität** | Kann |
| **Story** | US-023 |
| **Beschreibung** | Wenn `rich` verfügbar und TTY: aufgewerteter Monitor; abschaltbar via NO_COLOR/Plain. |
| **Abnahmekriterium** | Mit rich: Tabellen/Farben möglich; ohne rich oder NO_COLOR: Plaintext vollständig (A-021). |

---

## 4. Nicht-funktionale Anforderungen (Referenz)

Die vollständigen messbaren NFAs stehen in [`NFA.md`](./NFA.md).  
**Muss-NFAs sind abnahmepflichtig** gemeinsam mit den A-xxx.

| Gruppe | IDs (Auszug) | Kernziel |
|---|---|---|
| Performance | NFA-001–003 | status/topo < 3 s; Monitor 1–2 s; Heal-Idle < 5 s, Intervall 30 s |
| Skalierung | NFA-008, NFA-011 | 2–4 Nodes hart; kein Dauer-Server |
| Verfügbarkeit | NFA-012–016 | Best-effort Reboot ≤ 120 s (**A-038**); Idempotenz; Peer-down robust; Offline |
| Sicherheit | NFA-018–022, NFA-025–026 | Least Privilege; keine Secrets; argv-separierte Subprocesses; SCA |
| Datenschutz | NFA-028–030 | Keine PII; keine Telemetrie; lokale Dateien |
| A11y Terminal | NFA-032–033 | Keine reine Farbe; Plaintext-Fallback |
| Plattform | NFA-036, NFA-040–043 | CLI EN; macOS AS; Python 3.11+; symmetrisch; Install |
| Observability | NFA-045 | Exit-Codes gemäß **AD-3** (überschreibt NFA-A17 0/1/2-only) |
| Testbarkeit | NFA-048 | Fixture-Tests ohne Live-4-Node-HW in CI |

**ANNAHME A-X4:** NFA-045 wird durch AD-3 präzisiert: dokumentierte Codes **0 / 1 / 2 / 3** (nicht nur 0/1/2).

---

## 5. Traceability Story → Anforderung

| Story | MoSCoW | Abgedeckte A-IDs |
|---|---|---|
| US-001 | Muss | A-001, A-002, A-039 |
| US-002 | Muss | A-003, A-004, A-040, A-042 |
| US-003 | Muss | A-005, A-006, A-007, A-042 |
| US-004 | Muss | A-009, A-010, A-011, A-012, A-039, A-041 |
| US-005 | Muss | A-013, A-038, A-041 |
| US-006 | Muss | A-018, A-019, A-045 |
| US-007 | Muss | A-020, A-021 |
| US-008 | Muss | A-022, A-023, A-039 |
| US-009 | Muss | A-024, A-045 |
| US-010 | Muss | A-027, A-004, A-040 |
| US-011 | Muss | A-028, A-012, A-044 |
| US-012 | Muss | A-029, A-002 |
| US-013 | Soll | A-015, A-038 |
| US-014 | Soll | A-016 |
| US-015 | Soll | A-017 |
| US-016 | Soll | A-014, A-038 |
| US-017 | Soll | A-033 |
| US-018 | Soll | A-025, A-045 |
| US-019 | Soll | A-026 |
| US-020 | Muss | A-019, A-030, A-011 |
| US-021 | Soll | A-032, A-044, A-045 |
| US-022 | Kann | A-036 |
| US-023 | Kann | A-037, A-021 |
| US-024 | Muss | A-034, A-043 |
| US-025 | Muss | A-035 |
| US-026 | Soll | A-008, A-040 |

| A-ID | MoSCoW | Kurz |
|---|---|---|
| A-001 | Muss | TB-Info anzeigen |
| A-002 | Muss | TB ohne Admin |
| A-003 | Muss | init |
| A-004 | Muss | kein stilles Overwrite |
| A-005 | Muss | Config show/validate |
| A-006 | Muss | 2–4 Nodes |
| A-007 | Muss | Self-Erkennung |
| A-008 | Soll | TOML portabel + Beispiel |
| A-009 | Muss | up Bridge+IP |
| A-010 | Muss | up idempotent |
| A-011 | Muss | up ohne Link → Exit 3 |
| A-012 | Muss | up Rechte |
| A-013 | Muss | heal einmalig |
| A-014 | Soll | heal loop |
| A-015 | Soll | service install |
| A-016 | Soll | service uninstall |
| A-017 | Soll | service status |
| A-018 | Muss | status snapshot |
| A-019 | Muss | status Exit 3 bei peer down |
| A-020 | Muss | live monitor |
| A-021 | Muss | Plaintext / keine reine Farbe |
| A-022 | Muss | topo |
| A-023 | Muss | keine Kabel-Empfehlung |
| A-024 | Muss | doctor basis |
| A-025 | Soll | bench iperf3 |
| A-026 | Soll | bench ohne iperf3 |
| A-027 | Muss | Config-Fehler |
| A-028 | Muss | Admin melden |
| A-029 | Muss | RO ohne Root |
| A-030 | Muss | Partial-Cluster robust |
| A-031 | Soll | Writer-Lock |
| A-032 | Soll | SSH optional |
| A-033 | Soll | --json |
| A-034 | Muss | Installation symmetrisch |
| A-035 | Muss | Offline |
| A-036 | Kann | Action-Log/Rotation |
| A-037 | Kann | Rich-TUI |
| A-038 | Muss | Post-Reboot-Recovery best-effort |
| A-039 | Muss | Receptacle→Interface-Mapping |
| A-040 | Muss | Config-Pfad Default/Override |
| A-041 | Muss | Nur lokale Mutation |
| A-042 | Muss | schema_version Config |
| A-043 | Muss | Unsupported-Platform-Guard |
| A-044 | Muss | Security argv/Secrets |
| A-045 | Soll | Subprocess-Timeouts |

**Zählung:** **Muss 31** · **Soll 12** · **Kann 2** · **gesamt 45**

---

## 6. Domänenmodell (Kurzverweis)

Vollständig in [`DOMAENENMODELL.md`](./DOMAENENMODELL.md).

| Entität | Code | Persistenz |
|---|---|---|
| Cluster-Konfiguration | `ClusterConfig` | TOML |
| Node | `Node` | in Config |
| Thunderbolt-Port / -Link | `ThunderboltPort` / `ThunderboltLink` | Live |
| Bridge-Interface | `BridgeInterface` | Live / durch up/heal |
| Topologie | `Topology` | abgeleitet |
| Gesundheits-Schnappschuss | `HealthSnapshot` | flüchtig / optional Dump |
| Service-Zustand | `ServiceState` | LaunchAgent am Host |
| Heal-Aktion | `HealAction` | optional Audit |
| Diagnosebefund | `DoctorFinding` | Ausgabe |
| Bench-Ergebnis | `BenchResult` | optional |

**Kerninvarianten:** 2–4 Nodes; IPs eindeutig im Subnetz; genau ein Self-Match; Config = Soll-Wahrheit; Read-only mutiert nicht; SSH optional (INV-09).

---

## 7. Risiken (Kurzverweis)

Vollständig in [`RISIKEN.md`](./RISIKEN.md). Architektur adressiert prioritär:

| Prio | IDs | Thema |
|---|---|---|
| P0 | R-F04, R-D02, R-T02 | IP/Subnetz-Konflikt; ifconfig/networksetup; Privilegien |
| P0 | R-T01, R-F01, R-D01 | Receptacle-Mapping; TB-Parse-Drift |
| P1 | R-F03, R-T03, R-D03 | Heal-Races; LaunchAgent; launchctl |
| P1 | R-F02, R-T05 | Topo-Konfidenz; CI ohne Live-HW |

---

## 8. Randfälle (Kurzverweis)

Vollständig in [`RANDFAELLE.md`](./RANDFAELLE.md). QA-Fokus laut Mapping dort:

- `up` / `heal` / `service` / `monitor` — leere Zustände, Rechte, Locks, degraded Exit 3
- Config-Grenzen 0/1/2/4/5 Nodes, doppelte IPs, Injection in Interface-Namen
- SSH optional skip; iperf3 missing graceful

**Korrigierte Randfall-Semantik (gegenüber RF-A0):** Exit-Codes folgen **AD-3** (0 ok / 1 error / 2 usage / 3 degraded), nicht die RF-Nummerierung „1=Validierung, 2=System“. Inhaltliche Erwartungen der RF-Tabellen bleiben, Codes werden an AD-3 angeglichen.

---

## 9. ANNAHMEN (konsolidiert)

### 9.1 Aus Brief (verbindlich übernommen)

Brief-ANNAHMEN 1–23 gelten fort; Kernauszug:

| Nr. | Inhalt |
|---|---|
| 1 | Erfolg: 4 Nodes erreichbar; monitor korrekt; heal nach Reboot |
| 10 | status/topo < 3 s; Monitor 1–2 s; heal Default 30 s |
| 11 | 2–4 Nodes hart |
| 12 | Best-effort Heal; kein HA-SLA |
| 13 | Config ist Wahrheit; Operator versioniert selbst |
| 16 | Action-Log Default aus |
| 17–18 | Plaintext-Fallback; keine reine Farbkodierung |
| 19, 21 | CI lint+unit; Fixtures, kein Live-4-Node-Zwang in CI |

### 9.2 Lead-Analyst / Widerspruchslösung

| ID | Annahme | Begründung |
|---|---|---|
| **AD-1…AD-6** | siehe §2 | Offene Brief-Punkte + Analysten-Konflikte; AD-6 Config-Pfad |
| A-X1 | status Exit 3 bei peer down | AD-3 + Story S-2 geschärft |
| A-X2 | doctor Exit-Semantik worst-check | Skriptierbarkeit |
| A-X3 | CLI-Unterbefehlsnamen | Arbeitsbezeichner aus Stories |
| A-X4 | NFA-045 = Codes 0/1/2/3 | AD-3 schlägt NFA-A17 |
| A-X5 | Heal mutiert nur lokal, nie remote schreiben | → **A-041** (verbindlich) |
| A-X6 | `init --force` mit Backup | RF-A2, Config-Schutz; → A-004 geschärft |
| A-X7 | Subnetz-Overlap: doctor warnt; `up` bricht nicht auto ab, außer Preflight-Config-Konflikt (doppelte IP) | RF-A5 + R-F04: harte Validierung nur Config-intern; Host-Route-Overlap = Warnung |
| A-X8 | Unsupported Platform: mutate block Exit 2; read-only warn best-effort | → **A-043** |
| A-X9 | schema_version Pflicht in Config ab v1 | → **A-042** |
| A-X10 | Min. unterstützte macOS-Version: aktuelle stabile Releases; genaue Untergrenze in README/Architektur | R-T07; nicht blockierend für Analyse |
| A-X11 | Config-Pfad AD-6 + A-040 | XDG-ähnlich `~/.config/maccluster/cluster.toml` |
| A-X12 | Mapping-Ambiguität: fail closed bei Mutation | A-039; R-T01 |

---

## 10. OFFENE PUNKTE

Jeder Punkt mit **Entscheidungsvorschlag der Fabrik** (bei Autopilot verbindlich bis Gate 4).

| Nr. | Punkt | Auswirkung | Entscheidungsvorschlag der Fabrik | Klärung |
|---|---|---|---|---|
| **OP-1** | Konkrete Hostnames und HW-UUIDs der 4 Mac minis | Beispiel-Config, reales Abnahme-Szenario | Beispiel-`cluster.toml` mit Platzhaltern (`node-a` … `node-d`, IPs `10.42.0.1`–`.4`); reale Werte trägt Operator vor Gate 4 ein | IMPLEMENTIERUNG / ABNAHME |
| **OP-2** | Subnetz-Wahl final | Config-Default, Kollisionsrisiko | **`10.42.0.0/24`** (AD-1); in Config überschreibbar; `doctor` warnt bei Route-Overlap | **geschlossen (AD-1)** |
| **OP-3** | SSH-Probes Pflicht vs. optional | Monitor ohne Keys | **Optional, Default aus** (AD-2); nur Flag + Keys | **geschlossen (AD-2)** |
| **OP-4** | Exit-Code 3 (degraded) verbindlich? | Skripte, CI-Contracts | **Ja: 0/1/2/3** (AD-3); README-Tabelle Pflicht | **geschlossen (AD-3)** |
| **OP-5** | LaunchAgent User vs. System | Privilegien Bridge nach Login | **User-Domain** `gui/$(id -u)` (AD-4); falls Bridge root braucht: dokumentierter interaktiver sudo bei `up`/`heal`, Agent startet heal das elevatet wo möglich oder meldet klar | **geschlossen (AD-4)**; Detail Root-Helper → Architektur-ADR |
| **OP-6** | `up` ohne TB-Link: Exit 0 oder 3? | Operator-Erwartung | **Exit 3**, IP setzen wenn Bridge ok (AD-5) | **geschlossen (AD-5)** |
| **OP-7** | Erwartete physische Topologie (Kette/Stern/voll) für `Topology.complete` | Topo-Vollständigkeitsbegriff | **ANNAHME:** `complete` = alle Config-Peers per Ping erreichbar **oder** per Domain/Link matchbar; keine Pflicht auf Vollmesh-Kabelplan (DM-5) | ARCHITEKTUR (Feinalgorithmus) |
| **OP-8** | Minimale macOS-Version | Compatibility-Matrix | README listet getestete Version(en); `doctor` zeigt OS-Version; Untergrenze = neuestes macOS mit TB-Bridge auf Apple Silicon Mini zum Implementierungszeitpunkt, älter = best-effort Warnung | ARCHITEKTUR / README |
| **OP-9** | Ob NFA-006 (CPU/RSS) in CI messbar | QA-Plan | Nur manuelle Abnahme-Messung; CI prüft funktionale Tests | QA-Planung |
| **OP-10** | Default-Config-Pfad | A-027 prüfbar | **`~/.config/maccluster/cluster.toml`** (AD-6); Override `--config` / `MACCLUSTER_CONFIG` | **geschlossen (AD-6 / A-040)** |
| **OP-11** | Post-Reboot-Recovery als A-xxx | Erfolgskriterium Brief #1 | **A-038** + NFA-012; Service bleibt Soll, Recovery-Verhalten bei aktivem Heal ist Muss | **geschlossen (A-038)** |

---

## 11. Abnahmekriterien Projekt (DoD-Auszug)

Aus Brief K + QUALITAET, für Gate 4:

1. Alle **Muss**-Anforderungen A-xxx (inkl. A-038–A-044) nachgewiesen in `50-qa/TESTBERICHT.md`
2. Soll-Anforderungen im Vollausbau umgesetzt oder mit Gate-Begründung zurückgestellt
3. README: Install, Beispiel-`cluster.toml` (4 Nodes), Befehlsübersicht, Exit-Codes, read-only vs. admin, Config-Pfad (AD-6), Receptacle-Mapping/Override, best-effort (kein HA)
4. CI: Lint + Unit/Fixture-Tests grün (TB-Parse, Mapping, Config, Topo-Match ohne Live-4-Node-HW; NFA-048)
5. Keine kritischen offenen Bugs; Security-Baseline (A-044: keine Secrets, argv-Subprocess); SCA/Dependabot gemäß QUALITAET
6. Probelauf nach README auf Apple Silicon Mac mini (mind. 2-Node-Szenario; 4-Node wenn Hardware); Post-Reboot- oder Bridge-Loss-Szenario für A-038
7. Repo-Hygiene Welle 1 (QUALITAET 1.4): LICENSE, Lockfile, CI-Skeleton, Tests-Ordner, verify-Befehl — soweit anwendbar

---

## 12. Änderungsprotokoll Analyse

| Datum | Änderung |
|---|---|
| 2026-08-01 | Erstversion: Lead-Analyst konsolidiert Brief + USER-STORIES + DOMAENENMODELL + NFA + RISIKEN + RANDFAELLE; AD-1…AD-5 schließen OP-2/3/4/5/6 |
| 2026-08-01 | **Brief-Abgleich (Prüfer):** Lücken A-038–A-045; AD-6 Config-Pfad; Schärfung A-004/A-009/A-015/A-020/A-026/A-033/A-X2; OP-10/11 geschlossen; DoD ergänzt |
