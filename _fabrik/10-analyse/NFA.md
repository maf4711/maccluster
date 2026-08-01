# Nicht-funktionale Anforderungen — MacCluster

| Feld | Wert |
|---|---|
| Projekt | MacCluster (`maccluster`) |
| Phase | 1 ANALYSE |
| Quelle | `_fabrik/00-intake/BRIEF.md` (inkl. ANNAHMEN 10–18, 21) |
| Stand | 2026-08-01 |

Jede NFA hat eine ID (`NFA-xxx`), eine MoSCoW-Priorität und **genau ein prüfbares Abnahmekriterium**.  
Neue Defaults jenseits des Briefs sind als **ANNAHME** gekennzeichnet.

---

## 1. Leistungsübersicht

| Kategorie | Kernziel (v1) |
|---|---|
| Performance | CLI-Antwortzeiten im Sekundenbereich; Monitor-Refresh 1–2 s |
| Skalierung | Hart 2–4 Nodes; kein Ziel >4 |
| Verfügbarkeit | Best-effort Heal + LaunchAgent; kein HA-SLA |
| Sicherheit | OS-Rechte + Least Privilege; keine Secrets im Repo |
| Datenschutz | Keine personenbezogenen Daten; rein lokale Host-/Netzdaten |
| Barrierefreiheit | Plaintext ohne reine Farbabhängigkeit |
| Sprachen | CLI/README Englisch; Fabrik-Artefakte Deutsch |

---

## 2. Performance

### NFA-001 — Status- und Topologie-Latenz
| | |
|---|---|
| **Priorität** | Muss |
| **Beschreibung** | `maccluster status` und `maccluster topo` liefern auf einem gesunden Node (lokale Probes, ≤4 konfigurierte Peers) ein vollständiges Ergebnis in unter 3 Sekunden Wandzeit. |
| **Messung** | Stoppuhr / `time`-Messung über 5 aufeinanderfolgende Läufe; Median < 3,0 s. |
| **Abnahmekriterium** | Median-Wandzeit von `status` und `topo` jeweils < 3,0 s bei 2–4 konfigurierten Nodes und erreichbaren lokalen OS-Probes. |
| **Herkunft** | Brief ANNAHME 10 (F-01) |

### NFA-002 — Live-Monitor-Refresh
| | |
|---|---|
| **Priorität** | Muss |
| **Beschreibung** | Der Live-Monitor aktualisiert die Anzeige im Intervall 1–2 Sekunden (konfigurierbar im erlaubten Bereich; Default 1 s). Ein Refresh-Zyklus (lokale Probes + Rendering) darf das Intervall nicht dauerhaft überschreiten. |
| **Messung** | Beobachtung von ≥20 Zyklen; Anteil der Zyklen mit Dauer ≤ Intervall ≥ 90 %. |
| **Abnahmekriterium** | Bei Default-Intervall 1 s dauern ≥ 90 % der Refresh-Zyklen ≤ 1,0 s (4 Nodes, lokale Probes). |
| **Herkunft** | Brief ANNAHME 10 (F-01) |

### NFA-003 — Heal-Zyklus-Latenz
| | |
|---|---|
| **Priorität** | Muss |
| **Beschreibung** | Ein einzelner Heal-Durchlauf (Prüfen Bridge/IP + ggf. Korrektur) ist standardmäßig alle 30 s geplant (konfigurierbar). Die Ausführungsdauer eines Durchlaufs ohne notwendige Korrektur liegt unter 5 s. |
| **Messung** | Zeitstempel im optionalen Action-Log bzw. CLI-Verbose; 10 Idle-Durchläufe. |
| **Abnahmekriterium** | Median-Dauer Idle-Heal-Durchlauf < 5,0 s; Default-Intervall = 30 s und per Config änderbar. |
| **Herkunft** | Brief ANNAHME 10; Intervall-Default 30 s |

### NFA-004 — Doctor-Basisdiagnose
| | |
|---|---|
| **Priorität** | Soll |
| **Beschreibung** | `maccluster doctor` (Basis, ohne Bandwidth-Bench) schließt auf einem gesunden Node in unter 10 s ab. |
| **Messung** | `time maccluster doctor` (ohne `--bench` / ohne iperf3-Pfad). |
| **Abnahmekriterium** | Wandzeit < 10,0 s ohne Bench. |
| **Herkunft** | **ANNAHME** — Brief schweigt zu Doctor-Latenz; konservativer CLI-Richtwert für Diagnose-Tools. |

### NFA-005 — Bandwidth-Bench (optional)
| | |
|---|---|
| **Priorität** | Kann |
| **Beschreibung** | Wenn `iperf3` installiert ist, darf `bench` länger laufen; die Standard-Laufzeit pro Peer ist begrenzt und dokumentiert (Default 5 s Testdauer). |
| **Messung** | Config/CLI-Flag und Dokumentation. |
| **Abnahmekriterium** | Default-Testdauer ≤ 5 s pro Peer; Timeout und Abbruch dokumentiert und wirksam. |
| **Herkunft** | **ANNAHME** — Schutz vor unkontrolliertem Dauer-Bench; Brief nennt nur „wenn iperf3 vorhanden“. |

### NFA-006 — Ressourcenverbrauch (Idle / Monitor)
| | |
|---|---|
| **Priorität** | Soll |
| **Beschreibung** | Im Idle-Heal-Loop und im reinen Monitor-Betrieb bleibt der Prozess schlank: CPU-Mittelwert < 5 % eines Kerns im Steady-State, RSS < 100 MiB. |
| **Messung** | `ps`/`top` über ≥ 60 s Steady-State (4 Nodes, Default-Intervalle). |
| **Abnahmekriterium** | Steady-State: CPU-Mittel < 5 % eines Kerns und RSS < 100 MiB für `monitor` und `heal --loop` jeweils. |
| **Herkunft** | **ANNAHME** — Brief schweigt; konservativ für dauerhaft laufende LaunchAgent-/Monitor-Prozesse auf Mac mini. |

### NFA-007 — Startzeit CLI
| | |
|---|---|
| **Priorität** | Soll |
| **Beschreibung** | Kaltstart von `maccluster --help` bzw. `maccluster status` (Import + Parse) unter 1,5 s auf Apple Silicon. |
| **Messung** | `time` über 5 Kaltstarts (neuer Prozess). |
| **Abnahmekriterium** | Median-Kaltstart `--help` < 1,5 s. |
| **Herkunft** | **ANNAHME** — CLI-Usability; Python-3.11-Ökosystem realistischer Default. |

---

## 3. Skalierung & Lastprofil

### NFA-008 — Node-Anzahl (harte Grenze v1)
| | |
|---|---|
| **Priorität** | Muss |
| **Beschreibung** | Unterstützte Clustergröße: 2–4 Nodes. Konfigurationen mit <2 oder >4 Nodes werden abgelehnt bzw. mit klarem Fehler beendet; >4 ist kein Ziel von v1. |
| **Messung** | Config-Validierung und manuelle Tests mit 1, 2, 4, 5 Nodes. |
| **Abnahmekriterium** | 2 und 4 Nodes: alle Muss-Befehle funktionsfähig; 1 und 5 Nodes: Validierungsfehler mit verständlicher Meldung, Exit-Code ≠ 0. |
| **Herkunft** | Brief ANNAHME 11 (F-02); Auftrag „bis zu vier Mac minis“ |

### NFA-009 — Gleichzeitige CLI-Instanzen
| | |
|---|---|
| **Priorität** | Soll |
| **Beschreibung** | Mehrere lesende Instanzen (`status`, `monitor`, `topo`, `tb`, `doctor`) dürfen parallel laufen. Schreibende Operationen (`up`, `heal`, `service install`) serialisieren kritische Netzänderungen pro Host (Lock), um Race Conditions zu vermeiden. |
| **Messung** | Parallel-`status` während `monitor`; zwei gleichzeitige `heal` → einer wartet oder bricht sauber ab. |
| **Abnahmekriterium** | Lesende Parallelität ohne Fehler; konkurrierende Schreib-Ops erzeugen keinen inkonsistenten Netzwerkzustand (Lock oder dokumentierter Single-Writer). |
| **Herkunft** | **ANNAHME** — Brief schweigt; industrieller Default für System-CLIs mit Netz-Mutation. |

### NFA-010 — Datenvolumen & Log-Wachstum
| | |
|---|---|
| **Priorität** | Soll |
| **Beschreibung** | Persistenz bleibt klein: Config-Datei typisch < 16 KiB; optionales Action-Log (Default aus) mit Rotation, max. Gesamtgröße konfigurierbar (Default 5 MiB). |
| **Messung** | Dateigrößen nach 24 h simuliertem Heal-Loop mit Log an. |
| **Abnahmekriterium** | Bei Default-Rotation überschreitet das Action-Log 5 MiB nicht; Config bleibt im KiB-Bereich. |
| **Herkunft** | Brief ANNAHME 9 (Volumen klein) + ANNAHME 16 (optionales Log); Rotationslimit **ANNAHME** |

### NFA-011 — Keine Cloud-/Multi-Tenant-Last
| | |
|---|---|
| **Priorität** | Muss |
| **Beschreibung** | Kein Lastprofil für Remote-Clients, öffentliche APIs oder Multi-Tenant. Einzige „Last“: lokale Operator-CLI + optional ein LaunchAgent-Prozess pro Member. |
| **Messung** | Architektur-/Scope-Prüfung; keine Listener-Ports außer ggf. iperf3-Server temporär beim Bench. |
| **Abnahmekriterium** | Produkt startet keinen dauerhaften Netzwerk-Server für Dritte; kein Cloud-Endpoint. |
| **Herkunft** | Brief Out-of-Scope + Offline-first (G) |

---

## 4. Verfügbarkeit & Zuverlässigkeit

### NFA-012 — Best-effort Cluster-Erreichbarkeit
| | |
|---|---|
| **Priorität** | Muss |
| **Beschreibung** | Mit installiertem LaunchAgent (`heal --loop`) stellt das System Bridge und feste IP nach Reboot und nach erkannten Abweichungen best-effort wieder her. Es gibt **kein** quantifiziertes HA-/Uptime-Versprechen (kein 99,x %-SLA). |
| **Messung** | Manuelles Szenario: Reboot eines Members → nach Login/Agent-Start sind Bridge+IP gemäß Config wieder gesetzt (Timeout-Richtwert 120 s nach Agent-Start). |
| **Abnahmekriterium** | Nach Reboot und Agent-Start: innerhalb 120 s Bridge+IP gemäß Config; Abweichung wird im nächsten Heal-Zyklus korrigiert oder klar gemeldet. |
| **Herkunft** | Brief ANNAHME 12 (F-03); 120-s-Richtwert **ANNAHME** |

### NFA-013 — LaunchAgent-Restart-Verhalten
| | |
|---|---|
| **Priorität** | Soll |
| **Beschreibung** | Der Heal-Service ist als LaunchAgent so konfiguriert, dass er bei Prozessabsturz vom System neu gestartet wird (`KeepAlive` o. ä. gemäß macOS-Mechanik). |
| **Messung** | `launchctl` Status; Kill des Prozesses → erneutes Erscheinen. |
| **Abnahmekriterium** | Nach Kill des heal-Prozesses erscheint er innerhalb 60 s erneut (sofern Agent geladen). |
| **Herkunft** | Brief F4 + ANNAHME 12; 60-s-Fenster **ANNAHME** |

### NFA-014 — Fehlerverhalten ohne Cluster-Totalausfall
| | |
|---|---|
| **Priorität** | Muss |
| **Beschreibung** | Ausfall oder Unerreichbarkeit einzelner Peers darf lokale read-only Befehle und den lokalen Heal nicht hart abstürzen lassen; Status zeigt unerreichbare Nodes explizit. |
| **Messung** | Peer abstecken / falsche IP; `status`/`monitor`/`doctor` laufen zu Ende. |
| **Abnahmekriterium** | Bei 1 von N Peers down: Exit-Code dokumentiert (Warnung ≠ Crash); Ausgabe kennzeichnet den down-Node; Prozess beendet nicht mit unhandled Exception. |
| **Herkunft** | Brief Ziel „Zustand jederzeit prüfen“; Zuverlässigkeit CLI **ANNAHME**-Konkretisierung |

### NFA-015 — Offline-Fähigkeit
| | |
|---|---|
| **Priorität** | Muss |
| **Beschreibung** | Alle Kernfunktionen arbeiten ohne Internet und ohne Cloud-Dienste (nur lokales Netz / Thunderbolt-Mesh und macOS-Bordmittel; iperf3/SSH optional lokal). |
| **Messung** | Ausführung mit deaktiviertem WAN (z. B. Wi-Fi/Ethernet-Uplink aus; TB-Mesh an). |
| **Abnahmekriterium** | `status`, `topo`, `up`, `heal`, `monitor`, `doctor` (Basis) funktionieren ohne Internet-Route. |
| **Herkunft** | Brief G Technische Randbedingungen |

### NFA-016 — Idempotenz von up/heal
| | |
|---|---|
| **Priorität** | Muss |
| **Beschreibung** | Wiederholtes `up` bzw. `heal` auf bereits korrektem Zustand ändert das System nicht schädlich (keine doppelten Interfaces, keine IP-Konflikte durch erneutes Anwenden). |
| **Messung** | Zweimal `up` hintereinander; `ifconfig`/Config-Vergleich. |
| **Abnahmekriterium** | Zweites `up`/`heal` bei korrektem Zustand: Exit 0, keine zusätzliche fehlerhafte Netz-Konfiguration. |
| **Herkunft** | **ANNAHME** — industrieller Default für Netz-Bring-up-Tools. |

### NFA-017 — Backup & Datenverlust
| | |
|---|---|
| **Priorität** | Muss |
| **Beschreibung** | Die Config-Datei (`cluster.toml` o. ä.) ist die Single Source of Truth. Das Produkt erzwingt kein Server-Backup; der Operator versioniert die Datei selbst. Zerstörerische Schreibvorgänge an der Config erfolgen nicht ohne expliziten Befehl. |
| **Messung** | Code-/Verhaltensprüfung: keine automatische Cloud-Sicherung; `init` überschreibt bestehende Config nur mit Bestätigung/Flag. |
| **Abnahmekriterium** | Vorhandene Config wird ohne explizites Force-/Overwrite-Flag nicht still überschrieben; README beschreibt Operator-Backup (Dotfiles/Kopie). |
| **Herkunft** | Brief ANNAHME 13 (F-04); Force-Schutz **ANNAHME**-Konkretisierung |

---

## 5. Sicherheit

### NFA-018 — Kein Anwendungs-Login
| | |
|---|---|
| **Priorität** | Muss |
| **Beschreibung** | Authentifizierung erfolgt ausschließlich über den lokalen macOS-Benutzer (und ggf. sudo für privilegierte Ops). Kein App-Account, OAuth oder Session-Token. |
| **Messung** | Feature-/Code-Scope; CLI hat keine Login-Subcommands. |
| **Abnahmekriterium** | Keine Login-/Token-Flows im Produkt; privilegierte Befehle nutzen OS-Rechte und melden fehlende Rechte klar. |
| **Herkunft** | Brief H-01, ANNAHME 3 |

### NFA-019 — Least Privilege
| | |
|---|---|
| **Priorität** | Muss |
| **Beschreibung** | Read-only-Befehle (`status`, `monitor`, `topo`, `tb`, und `doctor` soweit möglich) laufen ohne Root. `up` / `heal` / `service install` dürfen Admin/sudo benötigen und müssen das **vor** der Aktion klar melden. |
| **Messung** | Ausführung als Normaluser; Versuch ohne sudo. |
| **Abnahmekriterium** | Read-only ohne Root erfolgreich (soweit OS es erlaubt); Schreib-Befehle ohne Rechte: verständliche Fehlermeldung, keine Teilmutation ohne Hinweis. |
| **Herkunft** | Brief G Technische Randbedingungen; QUALITAET §4.4 |

### NFA-020 — Keine Secrets im Code / Repo
| | |
|---|---|
| **Priorität** | Muss |
| **Beschreibung** | Keine Passwörter, Tokens oder privaten Schlüssel im Quellcode oder in Beispiel-Configs. SSH nutzt vorhandene Key-Pfade des Operators; Pfade dürfen in Config stehen, Schlüsselinhalte nicht. |
| **Messung** | Repo-Scan; Beispiel-`cluster.toml` ohne Secret-Material. |
| **Abnahmekriterium** | Keine Secrets in Repo/Beispielen; `.gitignore` deckt lokale Secret-/Override-Dateien ab, falls eingeführt. |
| **Herkunft** | QUALITAET §4.1; Brief H |

### NFA-021 — Eingabevalidierung an CLI- und Dateigrenzen
| | |
|---|---|
| **Priorität** | Muss |
| **Beschreibung** | Config-TOML, CLI-Argumente und Umgebungsvariablen werden an der Grenze validiert (Typen, IP-Syntax, Node-Anzahl, erlaubte Enums). Ungültige Eingaben → Exit ≠ 0, keine unhandled Exceptions. |
| **Messung** | Unit-/CLI-Tests mit malignen/ungültigen Inputs. |
| **Abnahmekriterium** | Für dokumentierte Invalid-Inputs jeweils Exit ≠ 0 und Fehlertext; kein Traceback als Normalfall für User-Fehler. |
| **Herkunft** | QUALITAET §4.2 |

### NFA-022 — Command-Injection-Resistenz bei OS-Aufrufen
| | |
|---|---|
| **Priorität** | Muss |
| **Beschreibung** | Aufrufe von `system_profiler`, `ifconfig`, `networksetup`, `ping`, `launchctl`, `iperf3` usw. erfolgen argument-separiert (keine Shell-String-Konkatenation mit untrusted Input). |
| **Messung** | Code-Review + Tests mit speziellen Zeichen in Hostnames/Pfaden. |
| **Abnahmekriterium** | Kein `shell=True`/äquivalente unsichere Konkatenation mit Config-/CLI-Werten; Nachweis im Security-Review. |
| **Herkunft** | **ANNAHME** / QUALITAET-Baseline — Pflicht für CLI die Systemtools wrappt. |

### NFA-023 — SSH-Remote-Probes
| | |
|---|---|
| **Priorität** | Soll |
| **Beschreibung** | SSH-Probes zu Peers sind optional (Config). Es werden bestehende SSH-Keys/Agent des OS genutzt; das Produkt speichert keine SSH-Passwörter. Timeout pro Probe begrenzt (Default 3 s). |
| **Messung** | Config ohne SSH → nur lokale Probes; mit SSH → Timeout greift. |
| **Abnahmekriterium** | Ohne SSH-Config voll nutzbar (lokale Sicht); mit SSH: Probe-Timeout Default 3 s, kein Password-Prompt-Hang > Timeout. |
| **Herkunft** | Brief E (SSH optional); Timeout **ANNAHME**; offener Brief-Punkt 3 (Pflicht vs. optional) → optional verbindlich für v1 |

### NFA-024 — Optionales Action-Audit-Log
| | |
|---|---|
| **Priorität** | Kann |
| **Beschreibung** | Optionales lokales Append-Log für `up`/`heal`-Aktionen (Default **aus**). Enthält Zeitstempel, Aktion, Ergebnis — keine Secrets. |
| **Messung** | Default-Config; Einschalten per Flag/Config. |
| **Abnahmekriterium** | Default: kein Action-Log geschrieben; bei Aktivierung: Einträge für up/heal ohne Secret-Inhalte. |
| **Herkunft** | Brief ANNAHME 16 (H-05) |

### NFA-025 — Abhängigkeiten & Lizenzen
| | |
|---|---|
| **Priorität** | Muss |
| **Beschreibung** | Abhängigkeiten minimal (stdlib primär; optional `rich`). Keine kritischen/hohen bekannten CVEs zur Abnahme; Lizenzen MIT/Apache-2.0/BSD/ISC o. freigegeben. Dependabot-fähig. |
| **Messung** | `pip-audit` / SCA + Lizenzcheck; `.github/dependabot.yml`. |
| **Abnahmekriterium** | SCA ohne offene critical/high (oder Gate-Freigabe); Dependabot-Config vorhanden; optional `rich` lizenzkonform. |
| **Herkunft** | QUALITAET §4.5–4.7; Brief Stack |

### NFA-026 — Verschlüsselung
| | |
|---|---|
| **Priorität** | Muss |
| **Beschreibung** | Keine zusätzliche App-Verschlüsselung von Config/Logs. Transport zu Peers nur über vom Operator eingerichtetes SSH (OS-Crypto). Thunderbolt-/Link-Verschlüsselung ist OS-/Hardware-Sache, nicht Produktumfang. |
| **Messung** | Scope-Prüfung. |
| **Abnahmekriterium** | Kein eigenes Crypto-Subsystem; Dokumentation stellt klar, dass Config im Klartext lokal liegt (Dateirechte OS). |
| **Herkunft** | Brief ANNAHME 15 (H-04) |

### NFA-027 — Dateirechte Config
| | |
|---|---|
| **Priorität** | Soll |
| **Beschreibung** | Beim Schreiben der Config setzt das Tool restriktive Rechte (Owner read/write only, `0600`), sofern es die Datei neu anlegt. |
| **Messung** | `ls -l` nach `init`. |
| **Abnahmekriterium** | Neu angelegte Config-Datei hat Modus `0600` (oder restriktiver). |
| **Herkunft** | **ANNAHME** — konservativer Default trotz „keine PII“, da Host-/Netzplan intern sein kann. |

---

## 6. Datenschutz & DSGVO

### NFA-028 — Keine personenbezogenen Daten im Verarbeitungssinn
| | |
|---|---|
| **Priorität** | Muss |
| **Beschreibung** | Verarbeitet werden technische Betriebsdaten: Hostnames, HW-UUIDs, IPs, Link-States, Domain-UUIDs, Timestamps. Keine Namen, E-Mails, Konten Dritter. Kein Profiling, kein Tracking. |
| **Messung** | Datenfluss in Domänenmodell / Code. |
| **Abnahmekriterium** | Persistierte und geloggte Felder beschränken sich auf technische Cluster-/Host-Attribute; kein Feld für natürliche Personen-Identität jenseits des OS-Hostnamens. |
| **Herkunft** | Brief H Personenbezogene Daten |

### NFA-029 — Keine Übermittlung an Dritte
| | |
|---|---|
| **Priorität** | Muss |
| **Beschreibung** | Keine Telemetrie, keine Crash-Reporter-Cloud, keine Update-Calls an Hersteller-Backends im Produktkern. Daten verlassen den lokalen Rechner nur, wenn der Operator SSH/iperf zu Peers aktiv nutzt (Cluster-intern). |
| **Messung** | Netzwerk-Beobachtung im Normalbetrieb (ohne Bench/SSH). |
| **Abnahmekriterium** | Im Normalbetrieb (`status`/`monitor`/`heal`) keine ausgehenden Verbindungen außer konfigurierten Peer-Probes im Cluster-Subnetz. |
| **Herkunft** | Offline-first; **ANNAHME**-Konkretisierung „keine Telemetrie“ |

### NFA-030 — Speicherort & Kontrolle
| | |
|---|---|
| **Priorität** | Muss |
| **Beschreibung** | Alle persistenten Daten liegen unter Kontrolle des Operators im lokalen Dateisystem (Config-Pfad dokumentiert; Logs lokal). Löschen = Dateien entfernen; kein serverseitiges Residuum. |
| **Messung** | README + Laufzeitpfade. |
| **Abnahmekriterium** | Dokumentierte Pfade; Deinstallation/`service uninstall` entfernt Agent-Plist; Nutzer kann Config/Logs manuell löschen. |
| **Herkunft** | Brief D Persistenz lokal |

### NFA-031 — DSGVO-Rollenklarheit
| | |
|---|---|
| **Priorität** | Soll |
| **Beschreibung** | Das Produkt ist lokales Operator-Tooling ohne Hosting durch die Fabrik. Es entsteht kein Auftragsverarbeitungs-Verhältnis mit der Fabrik als Auftragsverarbeiter für Endnutzer-Clusterdaten. README stellt „local-only, no cloud“ klar. |
| **Messung** | README-Absatz. |
| **Abnahmekriterium** | README enthält expliziten Hinweis: local-only, keine Cloud, keine Telemetrie. |
| **Herkunft** | Brief H Compliance „keine besonderen“; Konkretisierung **ANNAHME** |

---

## 7. Barrierefreiheit (Terminal)

### NFA-032 — Keine reine Farbkodierung
| | |
|---|---|
| **Priorität** | Muss |
| **Beschreibung** | Kritische Zustände (up/down, error/ok, link speed missing) sind immer über Text und/oder ASCII-/Unicode-Symbole erkennbar, nicht nur über Farbe. |
| **Messung** | Ausgabe mit `NO_COLOR=1` / ohne TTY-Color. |
| **Abnahmekriterium** | Bei deaktivierter Farbe bleiben alle Zustände in `status`/`monitor`/`doctor` unterscheidbar. |
| **Herkunft** | Brief ANNAHME 18 (I-03) |

### NFA-033 — Plaintext-Fallback
| | |
|---|---|
| **Priorität** | Muss |
| **Beschreibung** | Wenn `rich` nicht installiert ist oder Plaintext erzwungen wird, bleiben alle Muss-Befehle nutzbar mit lesbarer Monospace-Ausgabe. |
| **Messung** | Lauf ohne optional dependency. |
| **Abnahmekriterium** | Ohne `rich`: `status`, `monitor`, `topo`, `doctor` liefern vollständige Information als Plaintext. |
| **Herkunft** | Brief I + Kann „rich“; Fallback ANNAHME 17 |

### NFA-034 — NO_COLOR / FORCE_COLOR Konvention
| | |
|---|---|
| **Priorität** | Soll |
| **Beschreibung** | Respektiert die `NO_COLOR`-Umgebung (https://no-color.org). |
| **Messung** | `NO_COLOR=1 maccluster status`. |
| **Abnahmekriterium** | Bei gesetztem `NO_COLOR` keine ANSI-Farbcodes in der Ausgabe. |
| **Herkunft** | **ANNAHME** — Terminal-Industriekonvention. |

### NFA-035 — Screenreader-/Pipe-Freundlichkeit
| | |
|---|---|
| **Priorität** | Soll |
| **Beschreibung** | Bei stdout-Pipe (kein TTY) keine interaktiven Clear-Screen-Loops ohne Flush-Steuerung; Monitor im Pipe-Modus: zeilenweise Status oder Hinweis auf ungeeigneten Modus + Exit. |
| **Messung** | `maccluster monitor | head`. |
| **Abnahmekriterium** | Gepipter Monitor blockiert nicht endlos ohne Output; dokumentiertes Verhalten (einmaliger Dump oder Exit mit Hinweis). |
| **Herkunft** | **ANNAHME** — CLI-A11y/Automation-Default. |

---

## 8. Sprachen & Lokalisation

### NFA-036 — CLI- und Produkt-README-Sprache
| | |
|---|---|
| **Priorität** | Muss |
| **Beschreibung** | Alle CLI-Messages, Help-Texte, Fehlerausgaben und das Produkt-README sind Englisch. |
| **Messung** | Stichprobe Help + Fehlerpfade + README. |
| **Abnahmekriterium** | Nutzer-sichtbare Produkttexte Englisch; keine gemischtsprachigen Help-Pages. |
| **Herkunft** | Brief I UI-Sprache; Fabrik-Produktstandard |

### NFA-037 — Fabrik-Artefakte Deutsch
| | |
|---|---|
| **Priorität** | Muss |
| **Beschreibung** | Spezifikationen und Berichte unter `_fabrik/` bleiben Deutsch. |
| **Messung** | Artefakt-Review. |
| **Abnahmekriterium** | Analyse-/Architektur-/QA-Berichte auf Deutsch. |
| **Herkunft** | Fabrik-Standard / Brief I |

### NFA-038 — Keine i18n-Infrastruktur in v1
| | |
|---|---|
| **Priorität** | Muss |
| **Beschreibung** | Kein Gettext/Locale-Switch im Produkt v1. Einzige UI-Sprache: Englisch. |
| **Messung** | Scope. |
| **Abnahmekriterium** | Kein `--lang`/Locale-Subsystem erforderlich für Abnahme. |
| **Herkunft** | **ANNAHME** — Brief spezifiziert eine Sprache; kein Mehrsprachen-Scope. |

### NFA-039 — Zeichensatz
| | |
|---|---|
| **Priorität** | Soll |
| **Beschreibung** | Ausgabe UTF-8; Hostnames mit gängigen ASCII-Labels primär getestet. Nicht-ASCII-Hostnames werden nicht abgelehnt, sofern OS sie liefert, aber nicht als primäres Testziel. |
| **Messung** | UTF-8-Terminal; Fixture mit ASCII-Hostnames. |
| **Abnahmekriterium** | Keine Encoding-Crashs bei UTF-8-Locale; Dokumentation nennt ASCII-Hostnames als empfohlen. |
| **Herkunft** | **ANNAHME** |

---

## 9. Plattform, Kompatibilität & Betrieb

### NFA-040 — Zielplattform
| | |
|---|---|
| **Priorität** | Muss |
| **Beschreibung** | Unterstützt: macOS auf Apple Silicon Mac mini mit Thunderbolt/USB4. Nicht unterstützt: Linux, Windows, Intel-Mac als v1-Ziel. |
| **Messung** | README + Laufzeit-Guard (klare Fehlermeldung auf Unsupported). |
| **Abnahmekriterium** | Auf nicht unterstützter Plattform: Exit ≠ 0 mit klarer Meldung; auf Apple Silicon macOS: Kernpfade lauffähig. |
| **Herkunft** | Brief G + Out-of-Scope |

### NFA-041 — Python-Version
| | |
|---|---|
| **Priorität** | Muss |
| **Beschreibung** | Python 3.11+. |
| **Messung** | Packaging-Classifier / CI-Matrix mind. 3.11. |
| **Abnahmekriterium** | Installation und Tests grün unter Python 3.11+. |
| **Herkunft** | Brief G Stack |

### NFA-042 — Symmetrische Installation
| | |
|---|---|
| **Priorität** | Muss |
| **Beschreibung** | Dieselbe Package-Version und dieselbe Config-Struktur sind auf jedem Member installierbar; kein dedizierter Leader-Binary-Split. |
| **Messung** | Install-Pfad auf 2 Nodes identisch dokumentiert. |
| **Abnahmekriterium** | Ein Artefakt/Package für alle Member; Rollen self/peer nur aus Config/Identität, nicht aus verschiedenen Builds. |
| **Herkunft** | Brief A/G |

### NFA-043 — Auslieferung
| | |
|---|---|
| **Priorität** | Muss |
| **Beschreibung** | Installierbar via `pipx install` / `pip install -e .` / `install.sh` (mindestens einer der Wege im README als primär, andere erwähnt soweit unterstützt). |
| **Messung** | Probelauf nach README. |
| **Abnahmekriterium** | Frische Umgebung: Setup nach README → `maccluster --help` erfolgreich. |
| **Herkunft** | Brief J |

### NFA-044 — CI
| | |
|---|---|
| **Priorität** | Soll |
| **Beschreibung** | GitHub Actions: Lint + Unit-Tests bei Push; Integrationstests mit Fixtures (keine Pflicht auf Live-4-Node-Hardware in CI). |
| **Messung** | Workflow-Datei grün auf Default-Branch. |
| **Abnahmekriterium** | CI führt Lint + Unit-Tests aus; TB/Topo-Parser über Fixtures getestet. |
| **Herkunft** | Brief ANNAHME 19, 21 |

---

## 10. Observability (lokal)

### NFA-045 — Exit-Codes
| | |
|---|---|
| **Priorität** | Muss |
| **Beschreibung** | Dokumentierte Exit-Codes: 0 = Erfolg; ≠0 = Fehler/Validierung/teilweise Unerreichbarkeit je nach Befehl (semantik im README). |
| **Messung** | README-Tabelle + Tests. |
| **Abnahmekriterium** | Mindestens: 0 Erfolg, 1 generischer Fehler, 2 Nutzungs-/Config-Fehler (oder äquivalent dokumentiert). |
| **Herkunft** | **ANNAHME** — CLI-Standardpraxis. |

### NFA-046 — JSON-Output (optional)
| | |
|---|---|
| **Priorität** | Soll |
| **Beschreibung** | Wo Soll-Scope greift: `--json` für maschinenlesbare Status-/Doctor-Ausgabe, stabiles Schema pro Befehl (Versionierung über Feld `schema_version` oder dokumentierte Stabilität v1). |
| **Messung** | `maccluster status --json \| python -m json.tool`. |
| **Abnahmekriterium** | Gültiges JSON; dokumentierte Felder; Exit-Codes analog Text-Modus. |
| **Herkunft** | Brief Soll „optionales JSON-Output“; `schema_version` **ANNAHME** |

### NFA-047 — Verbose / Quiet
| | |
|---|---|
| **Priorität** | Kann |
| **Beschreibung** | `-v/--verbose` für Diagnose; `-q/--quiet` unterdrückt Nicht-Fehler-Ausgabe wo sinnvoll. |
| **Messung** | CLI-Help. |
| **Abnahmekriterium** | Mindestens eines von verbose/quiet implementiert und im Help dokumentiert. |
| **Herkunft** | **ANNAHME** |

---

## 11. Qualitätsbezogene NFAs (Testbarkeit)

### NFA-048 — Testbarkeit ohne Live-Cluster
| | |
|---|---|
| **Priorität** | Muss |
| **Beschreibung** | Kernlogik (Config-Parse, TB-Parse, Topo-Match, Health-Aggregation) ist mit Fixtures unit-/integrationstestbar ohne physische 4-Node-Hardware. |
| **Messung** | CI-Tests grün ohne TB-Hardware. |
| **Abnahmekriterium** | Fixture-basierte Tests decken Parser und Topo-Match ab; CI ohne Mac-mini-Farm grün. |
| **Herkunft** | Brief ANNAHME 21 |

### NFA-049 — Determinismus der Probes in Tests
| | |
|---|---|
| **Priorität** | Soll |
| **Beschreibung** | OS-Aufrufe sind hinter schmalen Ports/Adaptern mockbar; Zeit in Heal/Monitor injizierbar. |
| **Messung** | Unit-Tests ohne echte `ping`. |
| **Abnahmekriterium** | Mindestens Config- und Parser-Suites laufen offline deterministisch. |
| **Herkunft** | **ANNAHME** / QUALITAET Teststandards |

---

## 12. NFA-Matrix (Kurz)

| ID | Thema | Prio | Messgröße (Kurz) |
|---|---|---|---|
| NFA-001 | status/topo Latenz | Muss | < 3 s Median |
| NFA-002 | Monitor-Refresh | Muss | 1–2 s; ≥90 % ≤ Intervall |
| NFA-003 | Heal-Zyklus | Muss | Default 30 s; Idle < 5 s |
| NFA-004 | Doctor Basis | Soll | < 10 s |
| NFA-005 | Bench-Dauer | Kann | Default ≤ 5 s/Peer |
| NFA-006 | CPU/RSS Steady | Soll | < 5 % Kern; < 100 MiB |
| NFA-007 | CLI-Kaltstart | Soll | `--help` < 1,5 s |
| NFA-008 | 2–4 Nodes | Muss | Validierung hart |
| NFA-009 | Parallelität/Lock | Soll | Single-Writer |
| NFA-010 | Log-Rotation | Soll | Max 5 MiB Default |
| NFA-011 | Kein Server-Lastprofil | Muss | Kein Dauer-Listener |
| NFA-012 | Best-effort nach Reboot | Muss | Bridge/IP ≤ 120 s |
| NFA-013 | LaunchAgent KeepAlive | Soll | Restart ≤ 60 s |
| NFA-014 | Peer-down robust | Muss | Kein Crash |
| NFA-015 | Offline | Muss | Ohne Internet |
| NFA-016 | Idempotenz up/heal | Muss | 2. Lauf safe |
| NFA-017 | Config-Backup-Modell | Muss | Kein stilles Overwrite |
| NFA-018 | Kein App-Login | Muss | OS-Auth only |
| NFA-019 | Least Privilege | Muss | RO ohne Root |
| NFA-020 | Keine Secrets im Repo | Muss | Scan sauber |
| NFA-021 | Input-Validation | Muss | Exit ≠ 0 |
| NFA-022 | Keine Shell-Injection | Muss | argv-separiert |
| NFA-023 | SSH optional + Timeout | Soll | Default 3 s |
| NFA-024 | Action-Log Default aus | Kann | Opt-in |
| NFA-025 | SCA/Lizenzen | Muss | Kein crit/high |
| NFA-026 | Kein App-Crypto | Muss | Klartext lokal |
| NFA-027 | Config 0600 | Soll | nach init |
| NFA-028 | Keine PII | Muss | nur Tech-Daten |
| NFA-029 | Keine Telemetrie | Muss | keine WAN-Calls |
| NFA-030 | Lokale Datenhoheit | Muss | Dateien löschbar |
| NFA-031 | Local-only Doku | Soll | README-Hinweis |
| NFA-032 | Keine reine Farbe | Muss | NO_COLOR ok |
| NFA-033 | Plaintext-Fallback | Muss | ohne rich |
| NFA-034 | NO_COLOR | Soll | keine ANSI |
| NFA-035 | Pipe-Verhalten | Soll | kein Hang |
| NFA-036 | CLI Englisch | Muss | Help/Errors EN |
| NFA-037 | Fabrik DE | Muss | `_fabrik/` DE |
| NFA-038 | Kein i18n v1 | Muss | nur EN UI |
| NFA-039 | UTF-8 | Soll | kein Crash |
| NFA-040 | macOS AS only | Muss | Guard |
| NFA-041 | Python 3.11+ | Muss | CI |
| NFA-042 | Symmetrisch | Muss | ein Package |
| NFA-043 | Install-Pfad | Muss | README-Probelauf |
| NFA-044 | CI Lint+Unit | Soll | GHA grün |
| NFA-045 | Exit-Codes | Muss | dokumentiert |
| NFA-046 | --json | Soll | valid JSON |
| NFA-047 | verbose/quiet | Kann | Help |
| NFA-048 | Fixture-Tests | Muss | CI ohne HW |
| NFA-049 | Mockbare Probes | Soll | deterministisch |

**Zählung:** Muss 31 · Soll 14 · Kann 4 · **Summe 49**

---

## 13. ANNAHMEN (über Brief-Defaults hinaus)

| Nr. | Bezug | Annahme | Begründung |
|---|---|---|---|
| NFA-A1 | NFA-004 | Doctor-Basis < 10 s | Brief ohne Doctor-SLA; CLI-Diagnose-Richtwert |
| NFA-A2 | NFA-005 | iperf3 Default 5 s/Peer | Verhindert unbegrenzte Bench-Last |
| NFA-A3 | NFA-006 | CPU < 5 % / RSS < 100 MiB | Dauerprozess auf Mini akzeptabel |
| NFA-A4 | NFA-007 | Kaltstart `--help` < 1,5 s | Python-CLI-Usability |
| NFA-A5 | NFA-009 | File/Process-Lock für Writer | Verhindert Netz-Races |
| NFA-A6 | NFA-010 | Action-Log max 5 MiB | Brief „klein“; Rotation fehlt im Brief |
| NFA-A7 | NFA-012 | 120 s nach Agent-Start für Bridge/IP | Messbarer Reboot-Richtwert ohne HA-SLA |
| NFA-A8 | NFA-013 | KeepAlive-Restart ≤ 60 s | macOS LaunchAgent-üblich |
| NFA-A9 | NFA-016 | Idempotenz up/heal | Standard für Bring-up-Tools |
| NFA-A10 | NFA-017 | Kein stilles Config-Overwrite | Schutz vor Datenverlust |
| NFA-A11 | NFA-022 | argv-separierte Subprocesses | Injection-Baseline |
| NFA-A12 | NFA-023 | SSH-Probe-Timeout 3 s | Vermeidet Hänger; SSH bleibt optional |
| NFA-A13 | NFA-027 | Config-Datei `0600` | Defense-in-depth trotz „keine PII“ |
| NFA-A14 | NFA-029 | Keine Telemetrie | Offline-first konsequent |
| NFA-A15 | NFA-034/035 | NO_COLOR + Pipe-Verhalten | Terminal-Konventionen |
| NFA-A16 | NFA-038 | Kein i18n v1 | Eine Sprache spezifiziert |
| NFA-A17 | NFA-045 | Exit-Code-Semantik 0/1/2 | CLI-Standard |
| NFA-A18 | NFA-046 | `schema_version` in JSON | Stabile Automation |
| NFA-A19 | NFA-047 | verbose/quiet optional | Diagnose-Komfort |

Brief-ANNAHMEN 10–18, 19, 21 bleiben verbindlich und sind in die NFA-IDs überführt.

---

## 14. Offene Punkte (Gate / Architektur)

| Nr. | Punkt | Auswirkung auf NFA | Klärung |
|---|---|---|---|
| 1 | SSH-Probes Pflicht vs. optional | NFA-023 bereits „optional“ gesetzt; falls Auftraggeber Pflicht will → Monitor-Abhängigkeit & Timeout-NFA schärfen | ARCHITEKTUR / Gate 4 |
| 2 | Subnetz-Default `10.42.0.0/24` | Kein direkter NFA-Impact; Config-Validierung NFA-021 | ARCHITEKTUR |
| 3 | Konkrete Hostnames/HW-UUIDs | Abnahme-Messung NFA-001/012 auf Real-Hardware | IMPLEMENTIERUNG / ABNAHME |
| 4 | Ob RSS/CPU-Grenzen (NFA-006) in CI messbar sein müssen | Sonst nur manuelle Abnahme-Messung | QA-Planung |

---

## 15. Abgrenzung (bewusst keine NFA)

- Kein Web-UI-Performance-Budget  
- Kein öffentliches API-Rate-Limiting  
- Kein Multi-Region / Cloud-HA  
- Keine WCAG-Web-Konformität (kein Web)  
- Keine Penetration-Test-Pflicht Dritter (lokales Operator-Tool; Security-Baseline der Fabrik genügt)  
- Keine RTO/RPO-Garantie jenseits Config-Datei-Modell  
