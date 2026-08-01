# User Stories — MacCluster

| Feld | Wert |
|---|---|
| Projekt | MacCluster (`maccluster`) |
| Quelle | `_fabrik/00-intake/BRIEF.md` |
| Datum | 2026-08-01 |
| Rolle | Operator (einzige Rolle) |
| Projektmodus | Vollausbau (Muss + Soll) |
| CLI | `maccluster` (symmetrisch auf jedem Member) |

> **Hinweis:** Dateibesitz und Welle bleiben leer — Zuweisung erfolgt in der Planungsphase.  
> A-IDs werden nachgezogen, sobald `ANFORDERUNGEN.md` vorliegt; bis dahin verweisen Stories auf Brief-Funktionen F1–F7.

---

## Abdeckungsmatrix Funktionen → Stories

| Funktion / Thema | Priorität | Stories |
|---|---|---|
| F1 Thunderbolt-Hardware-Info | Muss | US-001 |
| F2 Cluster-Config / Node-Identität | Muss | US-002, US-003 |
| F2 Implizit: `init` | Muss | US-002 |
| F3 Bring-up (`up`) | Muss | US-004 |
| F4 Heal (einmalig) | Muss | US-005 |
| F4 Heal-Loop / „immer online“ | Soll | US-016 |
| F4 Service install/uninstall/status | Soll | US-013, US-014, US-015 |
| F5 Live-CLI-Monitor | Muss | US-007 |
| F5 Status (einmaliger Snapshot) | Muss | US-006 |
| F6 Topologie-Map (Auto-Detect) | Muss | US-008 |
| F7 Doctor/Diagnose (Basis) | Muss | US-009 |
| F7 Bandwidth-Bench (iperf3 optional) | Soll | US-018, US-019 |
| Soll: optionales JSON-Output | Soll | US-017 |
| Kann: Historie / Log-Rotation | Kann | US-022 |
| Kann: farbige Rich-TUI | Kann | US-023 |
| Implizit: Admin-/sudo-Meldung | Muss | US-011 |
| Implizit: Read-only ohne Root | Muss | US-012 |
| Implizit: fehlende/ungültige Config | Muss | US-010 |
| Implizit: Peer down / leerer Cluster | Muss | US-020 |
| Implizit: SSH-Probes optional | Soll | US-021 |
| Implizit: Installation & Offline | Muss | US-024, US-025 |
| Implizit: Config-Datei exportierbar | Soll | US-026 |
| Implizit: 2–4 Nodes Skalengrenze | Muss | US-003 (AK-3) |

---

# US-001 — Thunderbolt-Hardware-Info anzeigen

| Feld | Wert |
|---|---|
| ID | US-001 |
| MoSCoW | Muss |
| Abgedeckte Funktionen | F1 |
| Abgedeckte Anforderungen | (A-IDs nachziehen) |
| Abhängigkeiten (Story-IDs) | — |
| Welle | — |
| Dateibesitz | — |

## Story

Als **Operator** möchte ich **Thunderbolt-/USB4-Hardware-Informationen (Port-Fähigkeit, verhandelte Link-Geschwindigkeit, Receptacles/Ports, angeschlossene Peers) im Terminal sehen**, damit **ich die physische TB-Verkabelung und Link-Qualität prüfen kann, bevor ich den Cluster bring-up ausführe**.

## Akzeptanzkriterien

### AK-1 — Hardware-Übersicht
- **Given:** das CLI ist auf einem Apple-Silicon-Mac-mini installiert und mindestens ein Thunderbolt-Port ist systemseitig sichtbar
- **When:** der Operator `maccluster tb` (oder gleichwertigen Befehl) ausführt
- **Then:** die Ausgabe listet je Port/Receptacle mindestens: Port-Identität, Fähigkeit/Version, Interface-Zuordnung (soweit ermittelbar) und verhandelte Link-Geschwindigkeit bzw. „nicht verbunden“

### AK-2 — Peer-Sichtbarkeit
- **Given:** ein Peer ist per Thunderbolt-Kabel angeschlossen und vom OS erkannt
- **When:** der Operator die TB-Info abfragt
- **Then:** der angeschlossene Peer (bzw. Domain-/Link-Hinweis) erscheint in der Ausgabe; ohne Peer wird klar „kein Peer“ / „unconnected“ signalisiert (Text + Symbol, nicht nur Farbe)

### AK-3 — Ohne Admin-Rechte
- **Given:** der Operator ist als normaler macOS-Benutzer angemeldet (ohne sudo)
- **When:** er die TB-Info abfragt
- **Then:** der Befehl liefert die lesbaren Hardware-Daten ohne Privilege-Elevation; bei OS-seitigen Lücken erscheint eine klare Diagnose, kein stiller Abbruch ohne Meldung

## Hinweise zur Umsetzung

Datenquelle laut Brief: `system_profiler` / `ioreg` (lokal lesen). Keine Lösungsvorgabe für Parser-Details — Architektur.

---

# US-002 — Cluster initialisieren (`init`)

| Feld | Wert |
|---|---|
| ID | US-002 |
| MoSCoW | Muss |
| Abgedeckte Funktionen | F2 (init) |
| Abgedeckte Anforderungen | (A-IDs nachziehen) |
| Abhängigkeiten (Story-IDs) | — |
| Welle | — |
| Dateibesitz | — |

## Story

Als **Operator** möchte ich **mit einem Init-Befehl eine Cluster-Config-Vorlage (TOML) mit Cluster-Name, Subnetz, Interface und Node-Stubs anlegen**, damit **ich die festen TB-IPs und Node-Identitäten (Hostname/HW-UUID) einmalig pflegen und auf allen Members identisch nutzen kann**.

## Akzeptanzkriterien

### AK-1 — Vorlage erzeugen
- **Given:** noch keine Cluster-Config am erwarteten Pfad existiert (oder der Operator einen expliziten Zielpfad angibt)
- **When:** der Operator `maccluster init` ausführt
- **Then:** eine gültige TOML-Config-Datei wird geschrieben, die mindestens Cluster-Name, Subnetz, Interface-Name und 2–4 Node-Einträge (id, hostname, ip, hw_uuid-Platzhalter) enthält

### AK-2 — Kein stilles Überschreiben
- **Given:** am Zielpfad existiert bereits eine Config
- **When:** der Operator `init` ohne explizite Überschreib-Option ausführt
- **Then:** der Befehl bricht mit Exit-Code ≠ 0 ab und meldet, dass die Datei bereits existiert; der bestehende Inhalt bleibt unverändert

### AK-3 — Self-Node-Erkennung
- **Given:** `init` läuft auf einem Mac mini
- **When:** die Vorlage erzeugt wird
- **Then:** der lokale Hostname und/oder die HW-UUID des aktuellen Hosts werden soweit möglich als Self-Node vorausgefüllt (übrige Peers als Platzhalter)

## Hinweise zur Umsetzung

Default-Subnetz laut Brief-OFFENER-PUNKT: Vorschlag `10.42.0.0/24` (ANNAHME bis Architektur-Klärung). Config ist die Wahrheit (keine DB).

---

# US-003 — Cluster-Config laden, anzeigen und validieren

| Feld | Wert |
|---|---|
| ID | US-003 |
| MoSCoW | Muss |
| Abgedeckte Funktionen | F2 |
| Abgedeckte Anforderungen | (A-IDs nachziehen) |
| Abhängigkeiten (Story-IDs) | US-002 |
| Welle | — |
| Dateibesitz | — |

## Story

Als **Operator** möchte ich **die Cluster-Config anzeigen und vor Bring-up validieren lassen**, damit **ungültige IPs, doppelte Identitäten oder fehlende Pflichtfelder früh erkannt werden**.

## Akzeptanzkriterien

### AK-1 — Config anzeigen
- **Given:** eine gültige `cluster.toml` (bzw. dokumentierter Config-Pfad) existiert
- **When:** der Operator die Config anzeigt (z. B. `maccluster config show` / `status` mit Config-Abschnitt)
- **Then:** Cluster-Name, Subnetz, Interface und alle Nodes (id, hostname, ip, role self/peer) werden lesbar ausgegeben

### AK-2 — Validierungsfehler
- **Given:** die Config enthält z. B. doppelte IPs, ungültiges Subnetz, fehlende Node-IP oder >4 Nodes
- **When:** ein Befehl die Config lädt und validiert (`up`, `heal`, `monitor`, explizites `config validate`)
- **Then:** der Befehl scheitert mit Exit-Code ≠ 0 und benennt das konkrete Feld/den Konflikt; es werden keine Netzänderungen vorgenommen

### AK-3 — Skalengrenze 2–4 Nodes
- **Given:** die Config enthält 1 Node oder mehr als 4 Nodes
- **When:** die Validierung läuft
- **Then:** es erscheint eine klare Fehlermeldung, dass v1 nur 2–4 Nodes unterstützt (Brief ANNAHME 11)

### AK-4 — Node-Identität
- **Given:** Nodes sind über Hostname und/oder HW-UUID definiert
- **When:** der lokale Host die Config lädt
- **Then:** genau ein Node wird als `self` erkannt (Match Hostname und/oder HW-UUID); bei keinem oder mehreren Treffern schlägt die Validierung mit erklärender Meldung fehl

---

# US-004 — Cluster Bring-up (`up`)

| Feld | Wert |
|---|---|
| ID | US-004 |
| MoSCoW | Muss |
| Abgedeckte Funktionen | F3 |
| Abgedeckte Anforderungen | (A-IDs nachziehen) |
| Abhängigkeiten (Story-IDs) | US-002, US-003 |
| Welle | — |
| Dateibesitz | — |

## Story

Als **Operator** möchte ich **mit `up` die Thunderbolt-Bridge und die feste TB-IP für den lokalen Node gemäß Config aufbauen**, damit **der Mac mini im Cluster-Mesh unter der konfigurierten Adresse erreichbar wird**.

## Akzeptanzkriterien

### AK-1 — Bridge und IP setzen
- **Given:** eine gültige Config existiert und der Operator hat die nötigen Admin-Rechte (oder kann sie per sudo erteilen)
- **When:** er `maccluster up` ausführt
- **Then:** das konfigurierte Thunderbolt-Bridge-/Interface und die feste IP des Self-Nodes werden gesetzt bzw. bestätigt; die CLI meldet Erfolg inkl. Interface und IP

### AK-2 — Idempotenz
- **Given:** Bridge und IP sind bereits korrekt konfiguriert
- **When:** der Operator `up` erneut ausführt
- **Then:** der Befehl endet erfolgreich ohne destruktive Nebenwirkungen (kein unnötiges Tear-down) und meldet den bereits korrekten Zustand

### AK-3 — Fehlende Rechte
- **Given:** der Operator hat keine Admin-Rechte und sudo ist nicht verfügbar/abgelehnt
- **When:** er `up` ausführt
- **Then:** der Befehl bricht mit Exit-Code ≠ 0 ab und erklärt klar, dass Admin/sudo benötigt wird (kein stiller Partial-State ohne Hinweis)

### AK-4 — Self-IP nur lokal
- **Given:** die Config enthält mehrere Nodes mit unterschiedlichen IPs
- **When:** `up` auf diesem Host läuft
- **Then:** nur die IP und Bridge-Einstellungen des Self-Nodes werden lokal angewendet (keine Remote-Änderung an Peers ohne explizite Remote-Mechanismen)

---

# US-005 — Heal einmalig ausführen

| Feld | Wert |
|---|---|
| ID | US-005 |
| MoSCoW | Muss |
| Abgedeckte Funktionen | F4 (heal einmalig) |
| Abgedeckte Anforderungen | (A-IDs nachziehen) |
| Abhängigkeiten (Story-IDs) | US-003, US-004 |
| Welle | — |
| Dateibesitz | — |

## Story

Als **Operator** möchte ich **mit einem einmaligen Heal Bridge und feste IP gegen die Config abgleichen und bei Abweichung wiederherstellen**, damit **nach Reboot, Kabelzug oder Interface-Reset der Cluster-Node wieder online kommt**.

## Akzeptanzkriterien

### AK-1 — Drift erkennen und heilen
- **Given:** die Config ist gültig, aber Bridge fehlt, Interface ist down oder die Self-IP weicht ab
- **When:** der Operator `maccluster heal` (einmalig, ohne Loop) ausführt
- **Then:** die Abweichung wird erkannt, Korrekturmaßnahmen werden angewendet (soweit Rechte ausreichen) und das Ergebnis (geheilt / bereits ok / fehlgeschlagen) wird ausgegeben

### AK-2 — Bereits gesund
- **Given:** Bridge und Self-IP entsprechen der Config und das Interface ist up
- **When:** `heal` einmalig läuft
- **Then:** Exit-Code 0; Ausgabe signalisiert „healthy“ / keine Änderung nötig; keine unnötigen Netz-Resets

### AK-3 — Rechte und Fehler
- **Given:** eine Korrektur Admin-Rechte braucht, die nicht vorliegen
- **When:** `heal` läuft
- **Then:** Exit-Code ≠ 0 und verständliche Meldung zu fehlenden Rechten bzw. fehlgeschlagenem Schritt; bereits erfolgreiche Teilschritte werden nach Möglichkeit protokolliert

---

# US-006 — Status-Snapshot

| Feld | Wert |
|---|---|
| ID | US-006 |
| MoSCoW | Muss |
| Abgedeckte Funktionen | F5 (Status), F4 (HealthSnapshot) |
| Abgedeckte Anforderungen | (A-IDs nachziehen) |
| Abhängigkeiten (Story-IDs) | US-003 |
| Welle | — |
| Dateibesitz | — |

## Story

Als **Operator** möchte ich **einen einmaligen Cluster-Status (Nodes, Links, Erreichbarkeit) im Terminal sehen**, damit **ich ohne Dauer-Monitor schnell den Zustand prüfen kann**.

## Akzeptanzkriterien

### AK-1 — Statusinhalt
- **Given:** eine gültige Config mit 2–4 Nodes existiert
- **When:** der Operator `maccluster status` ausführt
- **Then:** für jeden konfigurierten Node erscheinen mindestens Identität (id/hostname), konfigurierte IP, Erreichbarkeit (up/down/unknown) und Zeitstempel der Prüfung; Self-Node ist gekennzeichnet

### AK-2 — Antwortzeit
- **Given:** bis zu 4 Nodes und lokale Probes
- **When:** `status` ausgeführt wird
- **Then:** die Ausgabe liegt typischerweise unter 3 Sekunden (Brief ANNAHME 10); Timeouts pro Peer sind begrenzt und blockieren nicht endlos

### AK-3 — Ohne Root
- **Given:** der Operator hat keine Admin-Rechte
- **When:** er `status` ausführt
- **Then:** der Befehl läuft ohne sudo und liefert den erreichbaren Status (Ping/Link-Lesen); fehlende Privilegien für Teilinfos werden pro Feld markiert, nicht als Totalabsturz

---

# US-007 — Live-CLI-Monitor

| Feld | Wert |
|---|---|
| ID | US-007 |
| MoSCoW | Muss |
| Abgedeckte Funktionen | F5 (Leuchtturm) |
| Abgedeckte Anforderungen | (A-IDs nachziehen) |
| Abhängigkeiten (Story-IDs) | US-006 |
| Welle | — |
| Dateibesitz | — |

## Story

Als **Operator** möchte ich **einen live aktualisierenden Terminal-Monitor mit Nodes, Links und Erreichbarkeit**, damit **ich den Cluster-Zustand dauerhaft im Blick behalte, ohne den Befehl manuell zu wiederholen**.

## Akzeptanzkriterien

### AK-1 — Periodische Aktualisierung
- **Given:** eine gültige Config existiert und der Monitor gestartet ist
- **When:** der Operator `maccluster monitor` ausführt
- **Then:** die Anzeige aktualisiert sich periodisch (Default-Refresh 1–2 s laut Brief ANNAHME 10) mit aktuellem Node-/Link-/Reachability-Stand

### AK-2 — Abbruch
- **Given:** der Monitor läuft
- **When:** der Operator mit üblicher Terminal-Unterbrechung (z. B. Ctrl+C) abbricht
- **Then:** der Prozess beendet sauber mit Exit-Code 0 (oder dokumentiertem Abbruch-Code); keine hängenden Kindprozesse

### AK-3 — Zustandswechsel sichtbar
- **Given:** ein Peer wechselt von erreichbar zu unerreichbar (oder umgekehrt)
- **When:** der nächste Monitor-Refresh abgeschlossen ist
- **Then:** der geänderte Zustand ist in der Ausgabe erkennbar (Text/Symbol; keine reine Farbkodierung kritischer Zustände)

### AK-4 — Leerer/Teil-Cluster
- **Given:** Config hat Nodes, aber noch kein Peer antwortet
- **When:** der Monitor läuft
- **Then:** alle Nodes werden mit down/unknown angezeigt; der Monitor stürzt nicht ab und zeigt Self-Status korrekt

## Hinweise zur Umsetzung

Leuchtturm-Funktion laut Brief. Optional Rich-TUI ist Kann (US-023); Plaintext-Fallback ist Pflicht.

---

# US-008 — Topologie-Map (Auto-Detect)

| Feld | Wert |
|---|---|
| ID | US-008 |
| MoSCoW | Muss |
| Abgedeckte Funktionen | F6 (Leuchtturm) |
| Abgedeckte Anforderungen | (A-IDs nachziehen) |
| Abhängigkeiten (Story-IDs) | US-001, US-003 |
| Welle | — |
| Dateibesitz | — |

## Story

Als **Operator** möchte ich **eine Topologie-Map mit Auto-Detect von Domain-UUID und Kabel-/Link-Zuordnung im Terminal sehen**, damit **ich erkenne, wie die Mac minis physisch per Thunderbolt verbunden sind**.

## Akzeptanzkriterien

### AK-1 — Topologie ausgeben
- **Given:** TB-Links und/oder Config-Nodes sind vorhanden
- **When:** der Operator `maccluster topo` ausführt
- **Then:** die Ausgabe zeigt die erkannten Links (Domain-UUID soweit verfügbar, lokale Ports/Receptacles, Peer-Bezug) als Karte oder strukturierte Liste

### AK-2 — Abgleich mit Config
- **Given:** Config-Nodes und Live-TB-Peers sind teilweise matchbar
- **When:** `topo` läuft
- **Then:** gematchte Nodes werden mit Config-Identität (id/hostname) verknüpft; ungematchte Links/Nodes werden als unmatched ausgewiesen

### AK-3 — Keine Kabelführungs-Empfehlung
- **Given:** die physische Verkabelung ist suboptimal
- **When:** `topo` läuft
- **Then:** es wird **keine** automatische physische Umverkabelungs-Empfehlung jenseits der erkannten Map ausgegeben (Out-of-Scope laut Brief)

### AK-4 — Antwortzeit und Rechte
- **Given:** normaler Benutzer ohne sudo
- **When:** `topo` ausgeführt wird
- **Then:** typisch &lt; 3 s; kein Root erforderlich für die lesbare Topologie-Ausgabe

---

# US-009 — Doctor / Diagnose (Basis)

| Feld | Wert |
|---|---|
| ID | US-009 |
| MoSCoW | Muss |
| Abgedeckte Funktionen | F7 (Basis) |
| Abgedeckte Anforderungen | (A-IDs nachziehen) |
| Abhängigkeiten (Story-IDs) | US-001, US-003, US-006 |
| Welle | — |
| Dateibesitz | — |

## Story

Als **Operator** möchte ich **mit `doctor` eine gebündelte Diagnose (Config, TB-Hardware, Interfaces, Erreichbarkeit, typische Fehlkonfigurationen) erhalten**, damit **ich Cluster-Probleme systematisch eingrenzen kann**.

## Akzeptanzkriterien

### AK-1 — Prüf-Checkliste
- **Given:** das CLI ist installiert (Config optional vorhanden)
- **When:** der Operator `maccluster doctor` ausführt
- **Then:** es werden mehrere Checks ausgeführt und je Check mit ok / warn / fail plus kurzer Hinweis ausgegeben (mindestens: Config vorhanden/gültig, Self-Node erkannt, TB-Ports sichtbar, Bridge/Interface-Zustand, Peer-Ping soweit möglich)

### AK-2 — Exit-Code bei Fehlern
- **Given:** mindestens ein kritischer Check schlägt fehl (z. B. keine Config, Self-Node unbekannt)
- **When:** `doctor` endet
- **Then:** Exit-Code ≠ 0; die Ausgabe listet die fehlgeschlagenen Checks nachvollziehbar

### AK-3 — Ohne Root soweit möglich
- **Given:** kein sudo
- **When:** `doctor` läuft
- **Then:** alle rein lesenden Checks laufen; Checks, die Rechte brauchen, werden als „skipped/needs admin“ markiert statt den gesamten Lauf abzubrechen

---

# US-010 — Fehlerfall: fehlende oder unlesbare Config

| Feld | Wert |
|---|---|
| ID | US-010 |
| MoSCoW | Muss |
| Abgedeckte Funktionen | Implizit (Fehlerpfad zu F2–F7) |
| Abgedeckte Anforderungen | (A-IDs nachziehen) |
| Abhängigkeiten (Story-IDs) | US-002 |
| Welle | — |
| Dateibesitz | — |

## Story

Als **Operator** möchte ich **bei fehlender, unlesbarer oder syntaktisch kaputter Config eine klare Fehlermeldung und einen Hinweis auf `init`**, damit **ich nicht mit kryptischen Stacktraces oder Partial-Netzänderungen konfrontiert werde**.

## Akzeptanzkriterien

### AK-1 — Config fehlt
- **Given:** am Standardpfad existiert keine Config
- **When:** ein config-abhängiger Befehl (`up`, `heal`, `status`, `monitor`, `topo` soweit config-abhängig) ausgeführt wird
- **Then:** Exit-Code ≠ 0; Meldung nennt den erwarteten Pfad und empfiehlt `init` (oder expliziten Config-Pfad)

### AK-2 — TOML-Syntaxfehler
- **Given:** die Config-Datei ist syntaktisch ungültig
- **When:** sie geladen wird
- **Then:** Exit-Code ≠ 0; Meldung nennt Datei und nach Möglichkeit Zeile/Feld; keine Netzänderung

### AK-3 — Unlesbar (Permissions)
- **Given:** die Config-Datei ist für den aktuellen Benutzer nicht lesbar
- **When:** ein Befehl sie öffnen will
- **Then:** Exit-Code ≠ 0 mit Permission-Hinweis

---

# US-011 — Admin-/sudo-Bedarf klar melden

| Feld | Wert |
|---|---|
| ID | US-011 |
| MoSCoW | Muss |
| Abgedeckte Funktionen | Implizit (G Technische Randbedingungen; F3, F4) |
| Abgedeckte Anforderungen | (A-IDs nachziehen) |
| Abhängigkeiten (Story-IDs) | US-004, US-005, US-013 |
| Welle | — |
| Dateibesitz | — |

## Story

Als **Operator** möchte ich **vor und bei schreibenden Netz-/Service-Operationen eindeutig sehen, ob und warum Admin-Rechte nötig sind**, damit **ich bewusst elevaten kann und Fehlschläge nicht als „Tool kaputt“ missverstehe**.

## Akzeptanzkriterien

### AK-1 — Schreibende Befehle
- **Given:** `up`, `heal` (bei Korrekturbedarf) oder `service install` wird ohne ausreichende Rechte gestartet
- **When:** die Operation Privilege Elevation bräuchte
- **Then:** die CLI meldet den Bedarf (Befehl/Aktion) und scheitert kontrolliert, falls Elevation ausbleibt

### AK-2 — Read-only Abgrenzung
- **Given:** der Operator ruft `tb`, `status`, `monitor`, `topo`, `doctor` (lesend) auf
- **When:** keine Admin-Rechte vorliegen
- **Then:** diese Befehle fordern **kein** sudo an und laufen mit den verfügbaren Daten

---

# US-012 — Read-only-Befehle ohne Root

| Feld | Wert |
|---|---|
| ID | US-012 |
| MoSCoW | Muss |
| Abgedeckte Funktionen | Implizit (G Technische Randbedingungen) |
| Abgedeckte Anforderungen | (A-IDs nachziehen) |
| Abhängigkeiten (Story-IDs) | US-001, US-006, US-007, US-008, US-009 |
| Welle | — |
| Dateibesitz | — |

## Story

Als **Operator** möchte ich **Status-, Monitor-, Topo-, TB- und Doctor-Befehle ohne Root nutzen**, damit **ich den Cluster im Alltag beobachten kann, ohne dauerhaft elevatete Shells zu brauchen**.

## Akzeptanzkriterien

### AK-1 — Kein Privilege-Prompt
- **Given:** normaler macOS-Benutzer ohne sudo-Session
- **When:** `tb`, `status`, `monitor`, `topo` und lesender `doctor` ausgeführt werden
- **Then:** es erscheint kein interaktiver sudo-Prompt; Exit bei Erfolg 0

### AK-2 — Dokumentation
- **Given:** das Produkt-README
- **When:** der Operator die Befehlsübersicht liest
- **Then:** read-only vs. admin-benötigende Befehle sind klar getrennt dokumentiert

---

# US-013 — LaunchAgent-Service installieren

| Feld | Wert |
|---|---|
| ID | US-013 |
| MoSCoW | Soll |
| Abgedeckte Funktionen | F4 (service install) |
| Abgedeckte Anforderungen | (A-IDs nachziehen) |
| Abhängigkeiten (Story-IDs) | US-005, US-016 |
| Welle | — |
| Dateibesitz | — |

## Story

Als **Operator** möchte ich **mit `service install` einen LaunchAgent einrichten, der Heal im Loop ausführt**, damit **der Cluster nach Login/Reboot best-effort „immer online“ bleibt**.

## Akzeptanzkriterien

### AK-1 — Installation
- **Given:** gültige Config und ausreichende Rechte für LaunchAgent-Installation
- **When:** der Operator `maccluster service install` ausführt
- **Then:** ein LaunchAgent ist registriert; `service status` meldet installed (und nach load möglichst running); Heal-Loop-Intervall entspricht Default (30 s) oder Config

### AK-2 — Idempotenz
- **Given:** der Service ist bereits installiert
- **When:** `service install` erneut läuft
- **Then:** Exit-Code 0 oder klarer „already installed“-Pfad ohne doppelte defekte Agents

### AK-3 — Rechte
- **Given:** Installation erfordert Admin-Rechte, die fehlen
- **When:** `service install` läuft
- **Then:** Exit-Code ≠ 0 mit klarer Rechte-Meldung

---

# US-014 — LaunchAgent-Service deinstallieren

| Feld | Wert |
|---|---|
| ID | US-014 |
| MoSCoW | Soll |
| Abgedeckte Funktionen | F4 (service uninstall) |
| Abgedeckte Anforderungen | (A-IDs nachziehen) |
| Abhängigkeiten (Story-IDs) | US-013 |
| Welle | — |
| Dateibesitz | — |

## Story

Als **Operator** möchte ich **den Heal-LaunchAgent mit `service uninstall` entfernen**, damit **kein Hintergrund-Heal mehr läuft, wenn ich den Cluster stilllege**.

## Akzeptanzkriterien

### AK-1 — Deinstallation
- **Given:** der Service ist installiert und ggf. geladen
- **When:** der Operator `maccluster service uninstall` ausführt
- **Then:** der LaunchAgent ist entladen/entfernt; `service status` meldet not installed; kein Heal-Loop-Prozess des Tools bleibt aktiv

### AK-2 — Bereits deinstalliert
- **Given:** kein Service ist installiert
- **When:** `service uninstall` läuft
- **Then:** Exit-Code 0 oder dokumentierter harmloser Hinweis; kein Crash

---

# US-015 — Service-Status abfragen

| Feld | Wert |
|---|---|
| ID | US-015 |
| MoSCoW | Soll |
| Abgedeckte Funktionen | F4 (service status) |
| Abgedeckte Anforderungen | (A-IDs nachziehen) |
| Abhängigkeiten (Story-IDs) | US-013 |
| Welle | — |
| Dateibesitz | — |

## Story

Als **Operator** möchte ich **den Zustand des LaunchAgent-Heal-Services abfragen**, damit **ich sehe, ob „immer online“ aktiv und laufend ist**.

## Akzeptanzkriterien

### AK-1 — Statusfelder
- **Given:** der Service ist installiert oder nicht
- **When:** der Operator `maccluster service status` ausführt
- **Then:** die Ausgabe enthält mindestens: installed ja/nein, running ja/nein (soweit ermittelbar), ggf. Label/Pfad und letztes bekanntes Intervall

### AK-2 — Ohne Root
- **Given:** normaler Benutzer
- **When:** `service status` läuft
- **Then:** die Abfrage funktioniert ohne sudo (soweit launchctl-User-Agent das erlaubt)

---

# US-016 — Heal im Loop (Dauerbetrieb)

| Feld | Wert |
|---|---|
| ID | US-016 |
| MoSCoW | Soll |
| Abgedeckte Funktionen | F4 (heal loop / Service-Kern) |
| Abgedeckte Anforderungen | (A-IDs nachziehen) |
| Abhängigkeiten (Story-IDs) | US-005 |
| Welle | — |
| Dateibesitz | — |

## Story

Als **Operator** möchte ich **Heal periodisch im Loop ausführen (CLI-Flag und/oder Service)**, damit **Drift nach Reboot oder Kabelereignissen ohne manuelles Eingreifen korrigiert wird**.

## Akzeptanzkriterien

### AK-1 — Loop-Modus
- **Given:** gültige Config
- **When:** der Operator `maccluster heal --loop` (oder gleichwertig) startet
- **Then:** Heal läuft wiederholt im konfigurierbaren Intervall (Default 30 s); jeder Zyklus protokolliert kurz das Ergebnis

### AK-2 — Sauberer Stopp
- **Given:** der Loop läuft im Vordergrund
- **When:** der Operator mit Ctrl+C abbricht
- **Then:** der Prozess beendet ohne hängende Threads; letzter Zyklus wird nicht halb-schreibend hinterlassen (best-effort atomar pro Zyklus)

### AK-3 — Kein HA-Versprechen
- **Given:** die Doku/CLI-Hilfe zum Loop
- **When:** der Operator sie liest
- **Then:** es wird best-effort (LaunchAgent-Restart) kommuniziert, kein Hochverfügbarkeits-Garantieversprechen (Brief ANNAHME 12)

---

# US-017 — Optionales JSON-Output

| Feld | Wert |
|---|---|
| ID | US-017 |
| MoSCoW | Soll |
| Abgedeckte Funktionen | Soll (JSON-Output); E Import/Export Status |
| Abgedeckte Anforderungen | (A-IDs nachziehen) |
| Abhängigkeiten (Story-IDs) | US-001, US-006, US-008, US-009 |
| Welle | — |
| Dateibesitz | — |

## Story

Als **Operator** möchte ich **bei geeigneten Befehlen maschinenlesbares JSON (`--json`) erhalten**, damit **ich Status/Topo/Doctor in Skripte und eigene Automatisierung überführen kann**.

## Akzeptanzkriterien

### AK-1 — JSON-Flag
- **Given:** ein unterstützter Befehl (mindestens `status`, `tb`, `topo`, `doctor`)
- **When:** der Operator `--json` setzt
- **Then:** stdout enthält gültiges JSON (parsebar); menschenlesbare Extra-Texte gehen nicht in denselben JSON-Stream (Fehler ggf. stderr + Exit-Code)

### AK-2 — Schema-Stabilität Basis
- **Given:** JSON-Status-Dump
- **When:** geparst
- **Then:** mindestens Felder für Nodes (id, ip, reachability) und Zeitstempel sind vorhanden und in der Doku beschrieben

### AK-3 — Keine öffentliche HTTP-API
- **Given:** das Produkt
- **When:** auf Netzwerk-APIs geprüft
- **Then:** es gibt **keine** öffentliche HTTP-API; JSON ist nur CLI-Ausgabe (Brief Out-of-Scope / E)

---

# US-018 — Bandwidth-Bench mit iperf3

| Feld | Wert |
|---|---|
| ID | US-018 |
| MoSCoW | Soll |
| Abgedeckte Funktionen | F7 (bench) |
| Abgedeckte Anforderungen | (A-IDs nachziehen) |
| Abhängigkeiten (Story-IDs) | US-003, US-006 |
| Welle | — |
| Dateibesitz | — |

## Story

Als **Operator** möchte ich **einen optionalen Bandwidth-Bench zwischen Nodes ausführen, wenn `iperf3` installiert ist**, damit **ich die effektive TB-Link-Leistung messe**.

## Akzeptanzkriterien

### AK-1 — Bench bei vorhandenem iperf3
- **Given:** `iperf3` ist im PATH und Ziel-Peer ist per TB-IP erreichbar
- **When:** der Operator `maccluster bench` (mit Ziel-Node/IP) ausführt
- **Then:** ein Durchsatz-Ergebnis (z. B. Gbit/s oder Mbit/s) wird ausgegeben; Exit-Code 0 bei erfolgreicher Messung

### AK-2 — Zielauswahl
- **Given:** mehrere Peers in der Config
- **When:** kein Ziel angegeben oder ungültiges Ziel
- **Then:** entweder interaktive/argumentbasierte Zielwahl greift, oder es erscheint eine klare Usage-Meldung mit Exit ≠ 0 — kein stiller Bench gegen localhost-only ohne Hinweis

---

# US-019 — Bench ohne iperf3 graceful

| Feld | Wert |
|---|---|
| ID | US-019 |
| MoSCoW | Soll |
| Abgedeckte Funktionen | F7 (Fehlerpfad bench) |
| Abgedeckte Anforderungen | (A-IDs nachziehen) |
| Abhängigkeiten (Story-IDs) | US-018 |
| Welle | — |
| Dateibesitz | — |

## Story

Als **Operator** möchte ich **eine klare Meldung, wenn `iperf3` fehlt**, damit **ich weiß, dass Bench optional ist und wie ich es nachrüste — ohne dass das restliche CLI unbenutzbar wird**.

## Akzeptanzkriterien

### AK-1 — Fehlendes iperf3
- **Given:** `iperf3` ist nicht installiert / nicht im PATH
- **When:** der Operator `bench` ausführt
- **Then:** Exit-Code ≠ 0; Meldung „iperf3 not found“ (o. ä.) plus kurzer Install-Hinweis; andere Befehle bleiben unberührt

### AK-2 — Doctor-Hinweis
- **Given:** `doctor` läuft ohne iperf3
- **When:** optionale Bench-Fähigkeit geprüft wird
- **Then:** der Check ist warn/skip (nicht fail des gesamten Clusters), mit Hinweis auf optionales Tool

---

# US-020 — Peer down und leere/partielle Zustände

| Feld | Wert |
|---|---|
| ID | US-020 |
| MoSCoW | Muss |
| Abgedeckte Funktionen | Implizit (F5, F6, Fehlerfälle) |
| Abgedeckte Anforderungen | (A-IDs nachziehen) |
| Abhängigkeiten (Story-IDs) | US-006, US-007, US-008 |
| Welle | — |
| Dateibesitz | — |

## Story

Als **Operator** möchte ich **bei unerreichbaren Peers, fehlenden Kabeln oder nur teilweise online Nodes stabile, verständliche Ausgaben**, damit **ich Teilausfälle vom Totalausfall des Tools unterscheiden kann**.

## Akzeptanzkriterien

### AK-1 — Peer nicht erreichbar
- **Given:** Self ist up, ein Peer antwortet nicht auf Ping
- **When:** `status` oder `monitor` läuft
- **Then:** der Peer wird als down/unreachable markiert; Exit-Code von `status` ist dokumentiert (z. B. ≠ 0 wenn irgendein Peer down — **ANNAHME:** Exit ≠ 0 bei mindestens einem down-Peer, Monitor bleibt laufend)

### AK-2 — Kein TB-Kabel
- **Given:** kein Thunderbolt-Peer ist physisch verbunden
- **When:** `tb` / `topo` laufen
- **Then:** Ausgabe zeigt leere Peer-Liste / unconnected Ports; Exit 0 für reine Info-Befehle, sofern Hardware lesbar ist

### AK-3 — Nur 2 von 4 Nodes verkabelt
- **Given:** Config hat 4 Nodes, nur 2 sind physisch im Mesh
- **When:** Monitor/Status laufen
- **Then:** erreichbare und unerreichbare Nodes sind unterscheidbar; Tool crasht nicht

---

# US-021 — Optionale SSH-Peer-Probes

| Feld | Wert |
|---|---|
| ID | US-021 |
| MoSCoW | Soll |
| Abgedeckte Funktionen | E (SSH optional); F5 Ergänzung |
| Abgedeckte Anforderungen | (A-IDs nachziehen) |
| Abhängigkeiten (Story-IDs) | US-006 |
| Welle | — |
| Dateibesitz | — |

## Story

Als **Operator** möchte ich **optionale SSH-basierte Remote-Probes zu Peers nutzen, wenn Keys vorhanden sind**, damit **ich über reines Ping hinaus Peer-seitige Diagnose holen kann — ohne SSH als Pflicht**.

## Akzeptanzkriterien

### AK-1 — Ohne SSH voll nutzbar
- **Given:** keine SSH-Keys / SSH deaktiviert in Config
- **When:** `status` / `monitor` / `doctor` laufen
- **Then:** lokale Probes (Ping, TB, Interface) genügen für Kernfunktion; keine harten Fehler nur wegen fehlendem SSH

### AK-2 — Mit SSH erweiterte Probe
- **Given:** SSH-Zugang zu einem Peer ist in Config aktiviert und Key funktioniert
- **When:** eine Remote-Probe ausgeführt wird
- **Then:** zusätzliche Peer-Infos (z. B. Remote-Hostname/Interface-Kurzstatus) erscheinen oder fließen in doctor; bei SSH-Fehler Fallback auf lokale Probe + Warnung

### AK-3 — Offener Punkt dokumentiert
- **Given:** Brief OFFENER PUNKT 3 (SSH Pflicht vs. optional)
- **When:** Architektur/README den Probe-Pfad beschreibt
- **Then:** Default ist **optional** (diese Story); Pflicht würde Auftraggeber-Klärung brauchen

## Hinweise zur Umsetzung

**ANNAHME (Brief OP-3):** SSH-Probes sind optional, nicht Pflicht für Monitor-Vollständigkeit.

---

# US-022 — Erweiterte Historie / Log-Rotation

| Feld | Wert |
|---|---|
| ID | US-022 |
| MoSCoW | Kann |
| Abgedeckte Funktionen | Kann (Historie/Log-Rotation); H Audit optional |
| Abgedeckte Anforderungen | (A-IDs nachziehen) |
| Abhängigkeiten (Story-IDs) | US-005, US-016 |
| Welle | — |
| Dateibesitz | — |

## Story

Als **Operator** möchte ich **optionale lokale Logs der heal/up-Aktionen mit einfacher Rotation**, damit **ich intermittierende Cluster-Probleme nachvollziehen kann, ohne die Platte zu füllen**.

## Akzeptanzkriterien

### AK-1 — Default aus
- **Given:** frische Installation
- **When:** heal/up laufen
- **Then:** ausführliches Append-Log ist standardmäßig deaktiviert (Brief ANNAHME 16) oder schreibt nur minimale Default-Traces laut Doku

### AK-2 — Aktivierbares Log
- **Given:** Logging ist in Config aktiviert
- **When:** heal/up Aktionen laufen
- **Then:** Einträge mit Zeitstempel und Aktion werden lokal appendiert

### AK-3 — Rotation
- **Given:** die Logdatei überschreitet die dokumentierte Größen-/Anzahlgrenze
- **When:** der nächste Schreibvorgang ansteht
- **Then:** Rotation greift (alte Datei umbenannt/gelöscht gemäß Policy); kein unbegrenztes Wachstum

---

# US-023 — Optionale Rich-TUI für Monitor

| Feld | Wert |
|---|---|
| ID | US-023 |
| MoSCoW | Kann |
| Abgedeckte Funktionen | Kann (Rich-TUI); F5 |
| Abgedeckte Anforderungen | (A-IDs nachziehen) |
| Abhängigkeiten (Story-IDs) | US-007 |
| Welle | — |
| Dateibesitz | — |

## Story

Als **Operator** möchte ich **optional eine farbige Rich-TUI im Monitor nutzen, wenn die Dependency erlaubt und installiert ist**, damit **die Live-Ansicht übersichtlicher wird — ohne Abhängigkeit für Kernfunktion**.

## Akzeptanzkriterien

### AK-1 — Fallback ohne rich
- **Given:** `rich` ist nicht installiert oder TTY unterstützt keine erweiterten Features
- **When:** `monitor` startet
- **Then:** Plaintext-Monitor funktioniert vollständig (US-007); kein harter Import-Error für den Nutzer

### AK-2 — Mit rich
- **Given:** `rich` ist verfügbar und stdout ist ein TTY
- **When:** `monitor` läuft
- **Then:** eine aufgewertete Darstellung ist aktiv (Tabellen/Farben), kritische Zustände bleiben ohne reine Farbabhängigkeit erkennbar (Symbole/Text)

### AK-3 — Abschaltbar
- **Given:** der Operator setzt eine No-Color-/Plain-Option oder `NO_COLOR`
- **When:** Monitor läuft
- **Then:** Ausgabe bleibt plain/lesbar ohne Pflichtfarben

---

# US-024 — Installation auf jedem Member

| Feld | Wert |
|---|---|
| ID | US-024 |
| MoSCoW | Muss |
| Abgedeckte Funktionen | Implizit (J Deployment; A Vision symmetrisch) |
| Abgedeckte Anforderungen | (A-IDs nachziehen) |
| Abhängigkeiten (Story-IDs) | — |
| Welle | — |
| Dateibesitz | — |

## Story

Als **Operator** möchte ich **MacCluster identisch auf jedem der bis zu vier Mac minis installieren (`pipx` / `pip install -e .` / `install.sh`)**, damit **derselbe Befehlssatz symmetrisch ohne dedizierten Leader verfügbar ist**.

## Akzeptanzkriterien

### AK-1 — Installationswege dokumentiert
- **Given:** das Produkt-Repository inkl. README
- **When:** der Operator die Install-Anleitung folgt
- **Then:** mindestens ein Weg (`pipx install`, `pip install -e .` oder `install.sh`) führt zu einem lauffähigen `maccluster`-Befehl im PATH

### AK-2 — Symmetrie
- **Given:** dieselbe Version ist auf zwei Nodes installiert und dieselbe Config-Struktur liegt vor
- **When:** der Operator auf beiden `maccluster status` ausführt
- **Then:** beide verstehen dieselbe Config und zeigen konsistente Self/Peer-Rollen relativ zum jeweiligen Host

### AK-3 — Plattformgrenze
- **Given:** die Doku
- **When:** Plattform-Support gelesen wird
- **Then:** nur macOS Apple Silicon Mac mini ist als Ziel genannt; Linux/Windows explizit out-of-scope

---

# US-025 — Offline-Betrieb ohne Cloud

| Feld | Wert |
|---|---|
| ID | US-025 |
| MoSCoW | Muss |
| Abgedeckte Funktionen | Implizit (G Offline; E keine Drittsystem-APIs) |
| Abgedeckte Anforderungen | (A-IDs nachziehen) |
| Abhängigkeiten (Story-IDs) | — |
| Welle | — |
| Dateibesitz | — |

## Story

Als **Operator** möchte ich **alle Kernfunktionen ohne Internet und ohne Cloud-Account nutzen**, damit **der Thunderbolt-Cluster rein lokal betrieben werden kann**.

## Akzeptanzkriterien

### AK-1 — Keine Cloud-Calls
- **Given:** der Host hat keine WAN-Route / DNS
- **When:** `init`, `tb`, `status`, `topo`, `doctor`, `up`, `heal` (lokal) ausgeführt werden
- **Then:** die Befehle funktionieren mit lokalen OS-Tools; es wird keine Cloud-API kontaktiert

### AK-2 — Kein Login
- **Given:** frische Installation
- **When:** ein beliebiger CLI-Befehl startet
- **Then:** es gibt keinen App-Login, OAuth oder Multi-User-Flow (nur OS-Benutzer + optionale SSH-Keys)

---

# US-026 — Config als Datei exportieren / teilen

| Feld | Wert |
|---|---|
| ID | US-026 |
| MoSCoW | Soll |
| Abgedeckte Funktionen | E Import/Export; F2 |
| Abgedeckte Anforderungen | (A-IDs nachziehen) |
| Abhängigkeiten (Story-IDs) | US-002, US-003 |
| Welle | — |
| Dateibesitz | — |

## Story

Als **Operator** möchte ich **die Cluster-Config als TOML-Datei kopieren und auf allen Members identisch ablegen**, damit **das Mesh mit festen IPs und Node-Identitäten konsistent bleibt (Dotfiles/Versionierung)**.

## Akzeptanzkriterien

### AK-1 — Datei ist portable Wahrheit
- **Given:** eine gültige Config auf Node A
- **When:** die Datei unverändert auf Node B am dokumentierten Pfad liegt
- **Then:** Node B lädt dieselbe Cluster-Definition; Self-Erkennung erfolgt über Hostname/HW-UUID

### AK-2 — Kein proprietäres Binärformat
- **Given:** die Config-Datei
- **When:** der Operator sie in einem Editor öffnet
- **Then:** sie ist als TOML menschenlesbar und editierbar

### AK-3 — Beispiel im Lieferumfang
- **Given:** README / Beispiele
- **When:** Abnahme vorbereitet wird
- **Then:** ein Beispiel-`cluster.toml` für bis zu 4 Nodes ist im Repo dokumentiert (Brief DoD)

---

## Traceability-Kurzliste (Story → MoSCoW)

| ID | Titel | MoSCoW |
|---|---|---|
| US-001 | Thunderbolt-Hardware-Info | Muss |
| US-002 | Cluster `init` | Muss |
| US-003 | Config laden/validieren | Muss |
| US-004 | Bring-up `up` | Muss |
| US-005 | Heal einmalig | Muss |
| US-006 | Status-Snapshot | Muss |
| US-007 | Live-Monitor | Muss |
| US-008 | Topologie-Map | Muss |
| US-009 | Doctor Basis | Muss |
| US-010 | Fehler: fehlende Config | Muss |
| US-011 | Admin-/sudo-Meldung | Muss |
| US-012 | Read-only ohne Root | Muss |
| US-013 | Service install | Soll |
| US-014 | Service uninstall | Soll |
| US-015 | Service status | Soll |
| US-016 | Heal-Loop | Soll |
| US-017 | JSON-Output | Soll |
| US-018 | Bench mit iperf3 | Soll |
| US-019 | Bench ohne iperf3 | Soll |
| US-020 | Peer down / Partial | Muss |
| US-021 | SSH-Probes optional | Soll |
| US-022 | Log-Historie/Rotation | Kann |
| US-023 | Rich-TUI Monitor | Kann |
| US-024 | Installation symmetrisch | Muss |
| US-025 | Offline ohne Cloud | Muss |
| US-026 | Config TOML teilen | Soll |

**Zählung:** Muss 16 · Soll 8 · Kann 2 · **gesamt 26**

---

## ANNAHMEN (in Stories getroffen)

| Nr. | Annahme | Begründung |
|---|---|---|
| S-1 | CLI-Unterbefehle heißen sinngemäß `tb`, `init`, `up`, `heal`, `status`, `monitor`, `topo`, `doctor`, `bench`, `service …`, `config show/validate` | Brief nennt Funktionen; konkrete Namen architekturstabil, hier als Arbeitsbezeichner |
| S-2 | `status` Exit ≠ 0 wenn ≥1 Peer down | übliche CLI-Semantik; Monitor bleibt trotzdem dauerhaft lauffähig |
| S-3 | SSH-Probes optional (Default) | Brief OFFENER PUNKT 3; risikoärmster Default für Monitor ohne Keys |
| S-4 | Default-Subnetz-Beispiel `10.42.0.0/24` in `init`-Vorlage | Brief OFFENER PUNKT 2 |
| S-5 | Heal-Loop-Default 30 s | Brief ANNAHME 10 |
| S-6 | JSON mindestens für `status`, `tb`, `topo`, `doctor` | Soll JSON-Output; Kern-Lese-Befehle |

---

## Offene Punkte (nur Auftraggeber)

| Nr. | Punkt | Betrifft Stories | Phase |
|---|---|---|---|
| OP-1 | Konkrete Hostnames und HW-UUIDs der 4 Minis | US-002, US-003, US-026, Abnahme | IMPLEMENTIERUNG / ABNAHME |
| OP-2 | Finales Subnetz (Vorschlag `10.42.0.0/24`) | US-002 | ARCHITEKTUR |
| OP-3 | SSH-Probes Pflicht oder optional? | US-021, US-006/007 | ARCHITEKTUR |
