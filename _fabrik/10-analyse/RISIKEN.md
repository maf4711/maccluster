# RISIKEN — MacCluster

| Feld | Wert |
|---|---|
| Projekt | maccluster |
| Phase | 1 ANALYSE |
| Quelle | `_fabrik/00-intake/BRIEF.md` (inkl. ANNAHMEN und OFFENE PUNKTE) |
| Stand | 2026-08-01 |

Bewertungsskala:

| Dimension | Stufen |
|---|---|
| **Eintrittswahrscheinlichkeit** | Niedrig · Mittel · Hoch |
| **Auswirkung** | Gering · Mittel · Hoch · Kritisch |
| **Risikostufe** | Produkt aus Wahrscheinlichkeit × Auswirkung (qualitativ) |

---

## 1. Übersicht Kernrisiken

| ID | Kurzname | Kategorie | Wahrscheinl. | Auswirkung | Stufe |
|---|---|---|---|---|---|
| R-F01 | TB-Parse bricht bei OS-Update | Fachlich / Technisch | Hoch | Hoch | **Hoch** |
| R-F02 | Falsche Topologie / Domain-UUID | Fachlich | Mittel | Hoch | **Hoch** |
| R-F03 | Heal-Races bei symmetrischem Mesh | Fachlich | Mittel | Hoch | **Hoch** |
| R-F04 | IP-/Subnet-Konflikt zerstört Erreichbarkeit | Fachlich | Mittel | Kritisch | **Kritisch** |
| R-F05 | Erwartung „HA-Cluster“ vs. Best-Effort | Fachlich / UX | Mittel | Mittel | **Mittel** |
| R-F06 | Scope-Creep zu Orchestrierung/RDMA | Fachlich | Mittel | Mittel | **Mittel** |
| R-T01 | Receptacle→Interface-Mapping unzuverlässig | Technisch | Hoch | Hoch | **Hoch** |
| R-T02 | `up`/`heal` ohne Admin schlägt still fehl | Technisch | Hoch | Hoch | **Hoch** |
| R-T03 | LaunchAgent-Loop unzuverlässig / doppelte Instanzen | Technisch | Mittel | Hoch | **Hoch** |
| R-T04 | Bridge-Änderungen stören andere Netze | Technisch | Mittel | Hoch | **Hoch** |
| R-T05 | CI ohne Live-TB-Hardware maskiert Bugs | Technisch / QA | Hoch | Mittel | **Hoch** |
| R-T06 | Python/stdlib-Parsing fragil, rich optional | Technisch | Mittel | Mittel | **Mittel** |
| R-T07 | macOS-Versionsdrift (TB/USB4-APIs) | Technisch | Mittel | Hoch | **Hoch** |
| R-D01 | system_profiler / ioreg Formatänderung | Abhängigkeit OS | Hoch | Hoch | **Hoch** |
| R-D02 | ifconfig / networksetup Privilegien & Syntax | Abhängigkeit OS | Mittel | Kritisch | **Kritisch** |
| R-D03 | launchctl / LaunchAgent-API-Verhalten | Abhängigkeit OS | Mittel | Hoch | **Hoch** |
| R-D04 | ping-Verhalten (ICMP, Timeout, Rechte) | Abhängigkeit OS | Niedrig | Mittel | **Niedrig** |
| R-D05 | iperf3 optional — Bench fehlt/irreführend | Abhängigkeit Dritt | Mittel | Gering | **Niedrig** |
| R-D06 | SSH-Probes (Keys, Host-Key, Timeout) | Abhängigkeit Dritt | Mittel | Mittel | **Mittel** |
| R-R01 | Privilegien-Eskalation / sudo-Missbrauch | Rechtlich / Security | Mittel | Hoch | **Hoch** |
| R-R02 | SSH-Keys und Host-Identitäten in Logs | Rechtlich / Privacy | Niedrig | Mittel | **Niedrig** |
| R-R03 | Netzwerk-Rekonfiguration ohne explizite Warnung | Rechtlich / Haftung | Mittel | Mittel | **Mittel** |
| R-R04 | Lizenz/Abhängigkeiten (rich, iperf3) | Rechtlich / Compliance | Niedrig | Mittel | **Niedrig** |

---

## 2. Fachliche Risiken

### R-F01 — Thunderbolt-Hardware-Info bricht bei OS-Update

| | |
|---|---|
| **Kategorie** | Fachlich / Technisch |
| **Bezug** | F1, F6, F7; Datenherkunft OS-Probes; ANNAHME 7 |
| **Beschreibung** | `system_profiler`/`ioreg`-Ausgabe (Version, Link-Speed, Ports/Receptacles, Domain-UUID) ändert sich zwischen macOS-Releases. Parser liefert leere oder falsche TB-Infos → Monitor/Topo/Doctor zeigen „kein TB“ obwohl Hardware ok. |
| **Eintrittswahrscheinlichkeit** | **Hoch** — Apple ändert SPI-ähnliche CLI-Outputs regelmäßig; kein stabiles Public-API-Versprechen. |
| **Auswirkung** | **Hoch** — Kernpfad F1/F6 entwertet; Operator diagnostiziert Hardware-Fehler statt Parser-Drift. |
| **Gegenmaßnahme** | 1) Parser versioniert mit Fixtures aus realen Samples (mehrere macOS-Versionen). 2) `doctor` meldet Parse-Fehler explizit (nicht „no hardware“). 3) Fallback-Kette: system_profiler → ioreg → Interface-Heuristik. 4) README: getestete macOS-Versionen; bei Drift Issue-Pfad. |
| **Frühindikator** | Unit-Tests mit nur einem Sample; Live-Abnahme auf einer OS-Version. |
| **Owner nächste Phase** | Architektur (Probe-Abstraktion) + QA (Fixture-Matrix) |

---

### R-F02 — Falsche Topologie / Domain-UUID-Zuordnung

| | |
|---|---|
| **Kategorie** | Fachlich |
| **Bezug** | F6 Leuchtturm; Node/ThunderboltLink; OFFENER PUNKT 1 (Hostnames/UUIDs) |
| **Beschreibung** | Auto-Detect der Kabel-Map (Domain-UUID, Peers, Receptacles) ordnet Links falsch zu (z. B. Line vs. Mesh, vertauschte Peers, Ghost-Links nach Kabelzug). Operator vertraut der Map und steckt um oder heilt falsch. |
| **Eintrittswahrscheinlichkeit** | **Mittel** — TB-Domain-UUIDs und Peer-Sichtbarkeit sind undokumentiert und situationsabhängig. |
| **Auswirkung** | **Hoch** — Leuchtturm-Feature irreführend; falsche Diagnose; ggf. unnötige `up`/`heal`-Eingriffe. |
| **Gegenmaßnahme** | 1) Topo zeigt Konfidenz/Quelle (Domain-UUID, Ping, Config) statt „Wahrheit“. 2) Config-Nodes (Hostname/HW-UUID) als Anker; Live-Links nur als Overlay. 3) Unklare Peers als `unknown` markieren. 4) Abnahmeszenario mit 2- und 4-Node-Fixtures. |
| **Frühindikator** | Topo-Algorithmus ohne „unknown“-Zustand; nur Happy-Path-Tests. |
| **Owner nächste Phase** | Domänenmodell + Architektur |

---

### R-F03 — Heal-Races im symmetrischen Mesh (kein Leader)

| | |
|---|---|
| **Kategorie** | Fachlich |
| **Bezug** | F3, F4; symmetrische Installation; ANNAHME 12 Best-effort |
| **Beschreibung** | Jeder Node führt `heal` (ggf. LaunchAgent-Loop) aus. Gleichzeitige Bridge-/IP-Änderungen auf mehreren Members erzeugen Flapping, doppelte IPs, oder gegenseitiges Zurücksetzen. Ohne Leader fehlt Koordination. |
| **Eintrittswahrscheinlichkeit** | **Mittel** — Default heal-Zyklus 30 s; mehrere Agents laufen parallel. |
| **Auswirkung** | **Hoch** — Cluster oszilliert; Erreichbarkeit schlechter als ohne Heal. |
| **Gegenmaßnahme** | 1) Heal ist **idempotent** und nur lokal (eigene Bridge/IP/Link prüfen). 2) Keine Remote-Schreibaktionen auf Peers. 3) Cooldown/Backoff bei wiederholtem Apply. 4) `service status` zeigt letzte Aktion/Ergebnis. 5) Doku: Best-effort, kein HA-Versprechen (ANNAHME 12). |
| **Frühindikator** | Heal schreibt remote via SSH; kein Idempotenz-Test. |
| **Owner nächste Phase** | Architektur (Heal-Semantik) |

---

### R-F04 — Feste IP-/Subnet-Konflikte

| | |
|---|---|
| **Kategorie** | Fachlich |
| **Bezug** | F2, F3; OFFENER PUNKT 2 (Subnetz `10.42.0.0/24`); ClusterConfig |
| **Beschreibung** | Feste TB-IPs kollidieren mit bestehendem LAN/VPN/Docker-Subnetz oder untereinander (doppelte Node-IP in Config). Bring-up „erfolgreich“, Routing bricht oder falscher Peer antwortet. |
| **Eintrittswahrscheinlichkeit** | **Mittel** — Default-Vorschlag kollidiert häufig mit Docker (`172.x`/`10.x`) und Heimnetzen; manuelle Config-Fehler möglich. |
| **Auswirkung** | **Kritisch** — Cluster unerreichbar; ggf. Störung anderer Dienste auf dem Mac. |
| **Gegenmaßnahme** | 1) `init`/`doctor` prüft Subnetz-Overlap gegen bestehende Routes/Interfaces. 2) Eindeutigkeitsprüfung Node-IPs in Config. 3) `up` dry-run / Preview vor Apply. 4) Default-Subnetz dokumentieren und bei Konflikt abbrechen mit klarer Meldung. 5) OFFENER PUNKT 2 in Architektur finalisieren. |
| **Frühindikator** | `up` setzt IP ohne Preflight. |
| **Owner nächste Phase** | Architektur + Implementierung Config-Validierung |

---

### R-F05 — Erwartung „HA-Cluster“ vs. Best-Effort-Tool

| | |
|---|---|
| **Kategorie** | Fachlich / UX |
| **Bezug** | A-Ziel „immer online“; ANNAHME 12; Out-of-Scope Inference/RDMA |
| **Beschreibung** | Operator liest „immer online“ / LaunchAgent als Hochverfügbarkeitsgarantie. Nach Reboot/Sleep/Kabelzug erwartet er automatische Recovery in Sekunden; Tool liefert nur Best-effort Heal. Enttäuschung und Support-Last. |
| **Eintrittswahrscheinlichkeit** | **Mittel** |
| **Auswirkung** | **Mittel** — Vertrauensverlust; keine Datenkorruption, aber falsche Betriebsentscheidungen. |
| **Gegenmaßnahme** | README und CLI-Help: „best-effort heal, no HA SLA“. `status` zeigt last-heal-result und known limitations (Sleep, Cable unplug). Abgrenzung zu Cluster-Managern (ANNAHME 2). |
| **Frühindikator** | Marketing-Texte ohne „best-effort“; fehlende Sleep/Reboot-Hinweise. |
| **Owner nächste Phase** | Doku + NFA |

---

### R-F06 — Scope-Creep zu Orchestrierung / Inference / RDMA

| | |
|---|---|
| **Kategorie** | Fachlich |
| **Bezug** | Out-of-Scope: exo, LLM-Inference, RDMA, Web-UI, Multi-Tenant |
| **Beschreibung** | Nähe zu AI-Mac-Mini-Clustern verleitet zu Features jenseits TB-Bridge/Monitor (Job-Scheduling, Model-Serving, RDMA-Enablement). Sprengt MVP/Vollausbau und Neutralität. |
| **Eintrittswahrscheinlichkeit** | **Mittel** |
| **Auswirkung** | **Mittel** — Termin, Scope, Architekturdrift. |
| **Gegenmaßnahme** | Out-of-Scope im Brief bindend; jede Erweiterung nur per Change Request. CLI-Namen und Doku vermeiden „orchestrator“/„inference“. |
| **Frühindikator** | Stories zu Remote-Exec jenseits optionaler SSH-Probes; GPU/RDMA-Code. |
| **Owner nächste Phase** | Planung (Backlog-Gate) |

---

## 3. Technische Risiken

### R-T01 — Receptacle→Interface-Mapping (Apple Silicon Mac mini)

| | |
|---|---|
| **Kategorie** | Technisch |
| **Bezug** | Brief G technische Randbedingungen; F1, F3, F6 |
| **Beschreibung** | Mapping physischer TB-Ports (Receptacles) zu `bridge`/`enX`-Interfaces ist undokumentiert und kann je Hardware-Revision und macOS variieren. Falsches Mapping → `up` konfiguriert falsches Interface; Topo zeigt vertauschte Ports. |
| **Eintrittswahrscheinlichkeit** | **Hoch** — explizit im Brief als kritisch markiert; wenig stabile Doku. |
| **Auswirkung** | **Hoch** — Bring-up und Diagnose auf falschem Device. |
| **Gegenmaßnahme** | 1) Mapping als isoliertes, testbares Modul mit Fixtures. 2) `tb`/`doctor` listet raw + mapped und erlaubt Override in Config. 3) Bei Ambiguität abbrechen statt raten. 4) Dokumentation der bekannten Mini-Layouts im README. |
| **Frühindikator** | Hardcoded Port-Nummern ohne Fixture-Tests. |
| **Owner nächste Phase** | Architektur + Implementierung |

---

### R-T02 — Privilegien: read-only vs. `up`/`heal`/`service`

| | |
|---|---|
| **Kategorie** | Technisch / Security |
| **Bezug** | Brief G; Rollenmatrix; F3, F4 |
| **Beschreibung** | `ifconfig`/`networksetup` und LaunchAgent-Installation benötigen Admin/sudo. Ohne klare Privilege-Detection schlagen Mutationen fehl (exit 0 mit No-Op, oder kryptische OS-Fehler). Operator glaubt Cluster sei up. |
| **Eintrittswahrscheinlichkeit** | **Hoch** — Standardnutzer ohne Admin; interaktives sudo in LaunchAgent unmöglich. |
| **Auswirkung** | **Hoch** — Silent failure des Kernpfads. |
| **Gegenmaßnahme** | 1) Preflight: Capability-Check vor Mutation. 2) Exit-Codes ≠ 0 + klare „needs admin“-Meldung. 3) Read-only Befehle nie sudo verlangen. 4) LaunchAgent: Root- vs. User-Agent-Entscheidung als ADR; Passwort nur interaktiv bei `service install`/`up`. 5) `--json` enthält structured error `E_PRIVILEGE`. |
| **Frühindikator** | Shell-Aufrufe ohne Auswertung von stderr/returncode. |
| **Owner nächste Phase** | Architektur (Privilege-Modell) + Security |

---

### R-T03 — LaunchAgent: Doppelstart, Crash-Loop, Sleep

| | |
|---|---|
| **Kategorie** | Technisch |
| **Bezug** | F4 Soll service install/uninstall/status; heal --loop |
| **Beschreibung** | LaunchAgent startet mehrfach, überlebt deinstall nicht, oder thrash’t bei Sleep/Wake (TB-Links down → heal-Sturm). Log-Flood und CPU-Last. |
| **Eintrittswahrscheinlichkeit** | **Mittel** |
| **Auswirkung** | **Hoch** — „Immer online“-Pfad instabil; Operator deinstalliert Tool. |
| **Gegenmaßnahme** | 1) Ein Label, `KeepAlive`/`ThrottleInterval` bewusst gesetzt. 2) `service status` prüft PID + last run. 3) Heal: Backoff, max actions/h, Sleep-Detection (optional). 4) uninstall entfernt plist vollständig. 5) Integrationstest mit Fixture-plist, kein Live-launchctl in CI zwingend. |
| **Frühindikator** | Kein Throttle; uninstall nur „unload“. |
| **Owner nächste Phase** | Architektur + Implementierung Service |

---

### R-T04 — Bridge-/IP-Änderungen stören andere Netzwerke

| | |
|---|---|
| **Kategorie** | Technisch |
| **Bezug** | F3 up; networksetup/ifconfig |
| **Beschreibung** | Falsches Interface oder Bridge-Create/Destroy kann Wi-Fi/Ethernet-Default-Route, VPN oder bestehende Bridges beeinträchtigen. |
| **Eintrittswahrscheinlichkeit** | **Mittel** |
| **Auswirkung** | **Hoch** — Operator verliert Internet/LAN-Zugang. |
| **Gegenmaßnahme** | 1) Nur TB-zugeordnete Interfaces anfassen (Allowlist aus Mapping). 2) `up` Preview + Bestätigung optional `--yes`. 3) `down`/`heal` stellen nur Cluster-State her, touch no default route. 4) Doctor warnt bei multi-homing-Anomalien. |
| **Frühindikator** | Globale `networksetup -setdnsservers` o. Ä. im Code. |
| **Owner nächste Phase** | Architektur + Security-Review |

---

### R-T05 — CI ohne echte 4-Node-Thunderbolt-Hardware

| | |
|---|---|
| **Kategorie** | Technisch / QA |
| **Bezug** | ANNAHME 21; K Abnahme; 2–4 Nodes |
| **Beschreibung** | GitHub Actions hat keine TB-Mac-minis. Unit/Integration mit Fixtures decken Parse/Config ab, nicht reales Mesh, Sleep, Kabelzug, LaunchAgent-on-device. Bugs erst bei Abnahme auf Hardware. |
| **Eintrittswahrscheinlichkeit** | **Hoch** — strukturell (CI-Umgebung). |
| **Auswirkung** | **Mittel** — verzögerte Abnahme; Nacharbeit; Rest-Risiko in Produktion. |
| **Gegenmaßnahme** | 1) Reiche Fixtures (2/3/4 Nodes, partial mesh, missing peer). 2) Manuelle Abnahme-Checkliste 60-abnahme mit realen Minis. 3) Contract-Tests für CLI-Exit-Codes und JSON-Schema. 4) OFFENER PUNKT 1 (reale UUIDs) vor Abnahme klären. |
| **Frühindikator** | „Grün in CI“ als alleiniger Abnahmebeleg. |
| **Owner nächste Phase** | QA + Abnahme |

---

### R-T06 — Python-Stack, optionales `rich`, fragile Subprocess-Nutzung

| | |
|---|---|
| **Kategorie** | Technisch |
| **Bezug** | Stack Python 3.11+; Kann: rich TUI; F5 Monitor |
| **Beschreibung** | Subprocess-Aufrufe ohne Timeouts hängen (ping/iperf3). `rich` als harte Dependency bricht Minimal-Install; fehlender Plaintext-Fallback verletzt A11y-ANNAHME 18. |
| **Eintrittswahrscheinlichkeit** | **Mittel** |
| **Auswirkung** | **Mittel** — hängende CLI; schlechte Offline/Minimal-UX. |
| **Gegenmaßnahme** | 1) Timeouts und Cancellation überall. 2) `rich` optional (extras); Plaintext default-fähig. 3) Keine reinen Farbcodes für kritische States. 4) stdlib-first, wenige Dependencies. |
| **Frühindikator** | `rich` in install_requires ohne extra; subprocess ohne timeout. |
| **Owner nächste Phase** | STACK/ADR + Implementierung |

---

### R-T07 — macOS-Versions- und Hardware-Drift (TB/USB4)

| | |
|---|---|
| **Kategorie** | Technisch |
| **Bezug** | Plattform nur Apple Silicon Mac mini; F1 |
| **Beschreibung** | USB4/TB5-Bezeichnungen, neue Port-Layouts, geänderte Bridge-Namen in künftigen macOS-Versionen. Produkt „Mac mini only“ schützt nicht vor OS-Drift. |
| **Eintrittswahrscheinlichkeit** | **Mittel** |
| **Auswirkung** | **Hoch** — schleichender Feature-Tod ohne Crash. |
| **Gegenmaßnahme** | Compatibility-Matrix im README; `doctor` meldet OS-Version + supported range; Parser feature-detect statt Versions-Hardcode wo möglich. |
| **Frühindikator** | Nur eine getestete OS-Version dokumentiert. |
| **Owner nächste Phase** | QA + Doku |

---

## 4. Abhängigkeiten von Dritten (OS-Tools & optionale Binaries)

### R-D01 — system_profiler / ioreg

| | |
|---|---|
| **Kategorie** | Abhängigkeit OS |
| **Bezug** | E Integrationen; F1, F6 |
| **Beschreibung** | Einzige Quelle für TB-Hardware-Details. Output-Schema und Keys sind nicht API-stabil. `ioreg` tief und maschinenlesbar, aber undokumentiert; `system_profiler` menschenlesbarer SP-Report, XML/JSON-Modi können Keys umbenennen. |
| **Eintrittswahrscheinlichkeit** | **Hoch** |
| **Auswirkung** | **Hoch** |
| **Gegenmaßnahme** | Dual-Source-Probes; golden fixtures; graceful degrade; doctor-Diff „raw vs parsed“. Kein Scraping von lokalisierten UI-Strings als einzige Quelle (englische/locale-stabile Keys bevorzugen). |
| **Frühindikator** | Regex auf lokalisierten Fließtext. |

---

### R-D02 — ifconfig / networksetup

| | |
|---|---|
| **Kategorie** | Abhängigkeit OS |
| **Bezug** | F3 up, F4 heal |
| **Beschreibung** | Schreibzugriff auf Interfaces/Bridges/IPs. Befehle und Service-Namen (`networksetup -listallhardwareports`) variieren; benötigen oft root. Fehlercodes und Meldungen inkonsistent. Falsche Nutzung kann Systemnetz zerstören (siehe R-T04). |
| **Eintrittswahrscheinlichkeit** | **Mittel** |
| **Auswirkung** | **Kritisch** |
| **Gegenmaßnahme** | Dünne Adapter-Schicht mit allowlisted Befehlen; dry-run; Unit-Tests mit aufgezeichneten stdout/stderr; nie `eval` auf Config-Strings; Least Privilege (nur nötige ifconfig-Flags). |
| **Frühindikator** | Zusammengebaute Shell-Strings mit User-Input. |

---

### R-D03 — launchctl / LaunchAgents

| | |
|---|---|
| **Kategorie** | Abhängigkeit OS |
| **Bezug** | F4 service |
| **Beschreibung** | `launchctl bootstrap/bootout/enable` Syntax unterscheidet User-Domain vs. System; macOS-Versionen ändern CLI. Falsches Domain → Service startet nicht nach Login/Reboot. |
| **Eintrittswahrscheinlichkeit** | **Mittel** |
| **Auswirkung** | **Hoch** |
| **Gegenmaßnahme** | Eine unterstützte Installationsvariante (ADR: per-user LaunchAgent vs. system); `service status` validiert realen Zustand; README mit Troubleshooting; Tests generieren plist und prüfen Inhalt (launchctl live optional manuell). |
| **Frühindikator** | Vermischung von `load` (legacy) und `bootstrap` ohne Version-Branch. |

---

### R-D04 — ping

| | |
|---|---|
| **Kategorie** | Abhängigkeit OS |
| **Bezug** | F5 Erreichbarkeit; HealthSnapshot |
| **Beschreibung** | ICMP kann blockiert sein (Firewall); macOS-`ping` Flags (`-c`, `-W`/`-t`) unterscheiden sich von Linux. Falsche Timeouts → false down/up. |
| **Eintrittswahrscheinlichkeit** | **Niedrig** |
| **Auswirkung** | **Mittel** |
| **Gegenmaßnahme** | macOS-spezifische Flags; konfigurierbarer Timeout; bei Permission/Firewall: Status `unreachable (probe failed)` vs. `down`; optional TCP-Connect auf bekannte Ports nur wenn spezifiziert (nicht Scope-Creep). |
| **Frühindikator** | Linux-`ping -W` auf macOS. |

---

### R-D05 — iperf3 (optional)

| | |
|---|---|
| **Kategorie** | Abhängigkeit Dritt-Tool |
| **Bezug** | F7 Soll bench wenn iperf3 vorhanden |
| **Beschreibung** | iperf3 ist nicht vorinstalliert (Homebrew/etc.). Server muss auf Peer laufen; Firewall; hohe Last auf TB-Link. Fehlende Binary darf Doctor nicht als Cluster-Fehler werten. Falsche Bandwidth-Zahlen bei CPU-Limit. |
| **Eintrittswahrscheinlichkeit** | **Mittel** (oft nicht installiert) |
| **Auswirkung** | **Gering** (Soll-Feature; Kerncluster ohne Bench nutzbar) |
| **Gegenmaßnahme** | Feature-Detection (`which iperf3`); klare „skipped: iperf3 not installed“; nie auto-install; Bench nur explizit `bench`; Warnung vor Last; Timeout/Kill. |
| **Frühindikator** | Hard dependency in packaging; bench im Default-doctor-Pfad als Fail. |

---

### R-D06 — SSH (optional, Config)

| | |
|---|---|
| **Kategorie** | Abhängigkeit Dritt / Security |
| **Bezug** | E SSH optional; OFFENER PUNKT 3; H Auth via OS + Keys |
| **Beschreibung** | Remote-Probes brauchen Keys, korrekte Hostnames, known_hosts, Timeout. Hänger bei Password-Prompt; Host-Key-Change bricht Monitor. Unklar ob SSH Pflicht → unvollständige Peer-Sicht ohne SSH. |
| **Eintrittswahrscheinlichkeit** | **Mittel** |
| **Auswirkung** | **Mittel** |
| **Gegenmaßnahme** | **ANNAHME R-A01:** SSH-Probes sind **optional** (Default aus bzw. nur wenn Config `ssh: true` + Key vorhanden). BatchMode=yes, ConnectTimeout, kein Password-Prompt. Monitor/Topo primär lokal (TB + Ping); SSH nur Anreicherung. OFFENER PUNKT 3 damit geschlossen als Default, Gate 4 bestätigt. |
| **Frühindikator** | `ssh` ohne `-o BatchMode=yes`; Monitor hängt. |

---

## 5. Rechtliche, Privacy- und Compliance-Risiken

### R-R01 — Privilegien-Eskalation und unsichere sudo-Nutzung

| | |
|---|---|
| **Kategorie** | Rechtlich / Security |
| **Bezug** | H Sicherheit; QUALITAET Baseline Least-Privilege |
| **Beschreibung** | CLI fordert sudo pauschal an, speichert Credentials, oder führt breite root-Shells aus. Erhöht Schaden bei Bugs/Config-Injection (Interface-Namen aus TOML). |
| **Eintrittswahrscheinlichkeit** | **Mittel** |
| **Auswirkung** | **Hoch** |
| **Gegenmaßnahme** | Keine Credential-Speicherung; sudo nur für einzelne allowlisted Kommandos; Validierung aller Config-Felder (IP, iface, hostname) vor Shell; keine Secrets in Repo; `.env` irrelevant — Config ohne Tokens. Security-Review auf Command-Injection. |
| **Frühindikator** | `sudo sh -c` mit interpolierter Config. |

---

### R-R02 — Host-Identitäten und Logs

| | |
|---|---|
| **Kategorie** | Rechtlich / Privacy |
| **Bezug** | H: keine PII; Host-/Netzdaten; ANNAHME 16 optionales Audit-Log |
| **Beschreibung** | HW-UUID, Hostnames, IPs, SSH-User in Logs/JSON-Dumps. Keine klassischen PII, aber gerätebezogene Identifikatoren; bei Teilen von Bug-Reports ungewollte Offenlegung der Lab-Topologie. |
| **Eintrittswahrscheinlichkeit** | **Niedrig** |
| **Auswirkung** | **Mittel** |
| **Gegenmaßnahme** | Audit-Log Default aus; `doctor --export` mit Redaction-Hinweis; README: Logs können Host-IDs enthalten; keine Cloud-Upload-Pfade. |
| **Frühindikator** | Telemetrie oder automatischer Upload. |

---

### R-R03 — Haftung bei Netzwerk-Rekonfiguration

| | |
|---|---|
| **Kategorie** | Rechtlich / Betrieb |
| **Bezug** | F3/F4 mutierende Befehle |
| **Beschreibung** | Operator-Tool ändert Systemnetz. Bei Datenverlust/Outage (falsches Subnetz) droht Erwartungs-Haftung, auch wenn Open-Source/lokal. |
| **Eintrittswahrscheinlichkeit** | **Mittel** (Missverständnisse) |
| **Auswirkung** | **Mittel** |
| **Gegenmaßnahme** | LICENSE + README Disclaimer: local admin tool, operator responsibility; Preview vor destruktiven Ops; klare Exit-Codes; kein Autostart von `up` ohne Install-Intent. |
| **Frühindikator** | Mutierende Defaults ohne Bestätigung in interaktiver Session. |

---

### R-R04 — Lizenzen von Dependencies und iperf3

| | |
|---|---|
| **Kategorie** | Rechtlich / Compliance |
| **Bezug** | QUALITAET 4.5; optional rich; externes iperf3 |
| **Beschreibung** | `rich` (MIT — ok) oder künftige Deps mit copyleft; iperf3 (BSD-ähnliche Lizenz) wird nicht gebündelt, aber Doku muss Trennung klarstellen. GPL-Deps würden Gate blockieren. |
| **Eintrittswahrscheinlichkeit** | **Niedrig** |
| **Auswirkung** | **Mittel** (Abnahme-Blocker bei Verstoß) |
| **Gegenmaßnahme** | stdlib-first; Lizenzcheck vor Dependency; iperf3 nicht vendored; Dependabot + `gen_dependabot.py`; LICENSE im Produkt-Root (DoD G1). |
| **Frühindikator** | Ungeprüfte PyPI-Abhängigkeit mit GPL. |

---

## 6. Projektrisiken (Fabrik-Pipeline / Lieferung)

### R-P01 — Abnahme blockiert durch fehlende reale Node-IDs

| | |
|---|---|
| **Kategorie** | Projekt |
| **Bezug** | OFFENER PUNKT 1; Erfolgskriterium ANNAHME 1 |
| **Beschreibung** | Ohne Hostnames/HW-UUIDs der 4 Minis bleibt Abnahme-Szenario synthetisch; Erfolgskriterium „4 Nodes erreichbar“ nicht belegbar. |
| **Eintrittswahrscheinlichkeit** | **Mittel** |
| **Auswirkung** | **Hoch** (Gate 4 verzögert) |
| **Gegenmaßnahme** | Beispiel-`cluster.toml` mit Platzhaltern + separates reales Abnahme-Toml; Checkliste fordert Operator-Werte vor Gate 4. |

---

### R-P02 — Autopilot trifft Subnetz-/SSH-Defaults falsch

| | |
|---|---|
| **Kategorie** | Projekt / ANNAHME |
| **Bezug** | OFFENE PUNKTE 2–3; Gates autopilot |
| **Beschreibung** | Defaults `10.42.0.0/24` und SSH-optional werden erst bei finaler Abnahme geprüft; Ablehnung erzwingt CR/Nacharbeit. |
| **Eintrittswahrscheinlichkeit** | **Mittel** |
| **Auswirkung** | **Mittel** |
| **Gegenmaßnahme** | ANNAHMEN in RISIKEN/NFA/Architektur gebündelt; konfigurierbar halten; keine Hardcodes ohne Override. |

---

## 7. ANNAHMEN (über den Brief hinaus)

| ID | Annahme | Begründung |
|---|---|---|
| R-A01 | SSH-Probes sind optional (Default: lokal TB + Ping; SSH nur bei expliziter Config und vorhandenem Key, BatchMode) | OFFENER PUNKT 3; Monitor muss ohne SSH nutzbar sein (Brief: Keys „wenn vorhanden“) |
| R-A02 | Subnetz-Default `10.42.0.0/24` mit Preflight-Overlap-Check; bei Konflikt Abbruch | OFFENER PUNKT 2; konservativ und dokumentiert |
| R-A03 | Heal mutiert nur den lokalen Node; keine verteilte Konsens-/Leader-Wahl in v1 | Symmetrie-Anforderung + Best-effort (ANNAHME 12) |
| R-A04 | iperf3 wird nicht installiert oder gebündelt; Bench ist skip-ok | Soll-Feature „wenn vorhanden“ |
| R-A05 | Unterstützte Zielplattform v1: Apple Silicon Mac mini, aktuelle stabile macOS-Versionen; ältere Intel-Macs out of scope | Brief G; reduziert Matrix |

---

## 8. Risikomatrix (Priorisierung für Architektur)

| Priorität | IDs | Handlungsbedarf in Phase 2 |
|---|---|---|
| P0 | R-F04, R-D02, R-T02 | Privilege- und Network-Apply-Design, Preflight, Allowlist |
| P0 | R-T01, R-F01, R-D01 | Probe-Abstraktion, Fixtures, Mapping-Modul |
| P1 | R-F03, R-T03, R-D03 | Idempotentes Heal, LaunchAgent-ADR |
| P1 | R-F02, R-T05 | Topo-Konfidenz, Abnahme-Hardware-Plan |
| P2 | R-D05, R-D06, R-R02–R-R04 | Optionale Pfade, Lizenz, Redaction |
| P2 | R-F05, R-F06, R-P01–P02 | Doku, Scope, ANNAHMEN-Gate |

---

## 9. Offene Punkte (menschliches Gate / Abnahme)

| Nr. | Punkt | Bezug Risiko | Klärung |
|---|---|---|---|
| 1 | Reale Hostnames und HW-UUIDs der 4 Minis | R-P01, R-F02 | IMPLEMENTIERUNG / ABNAHME |
| 2 | Finales Subnetz (Default 10.42.0.0/24 ok?) | R-F04, R-A02 | ARCHITEKTUR / Gate 4 |
| 3 | SSH-Probes optional bestätigen (R-A01) | R-D06 | ARCHITEKTUR / Gate 4 |
| 4 | LaunchAgent: User-Domain vs. system-wide | R-T03, R-D03 | ARCHITEKTUR (ADR) |
| 5 | Minimale unterstützte macOS-Version | R-T07 | ARCHITEKTUR / README |
