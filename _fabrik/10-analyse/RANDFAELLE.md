# Rand- und Fehlerfälle — MacCluster

| Feld | Wert |
|---|---|
| Projekt | MacCluster (`maccluster`) |
| Quelle | `_fabrik/00-intake/BRIEF.md` |
| Stand | 2026-08-01 |
| Zweck | Prüfbare Erwartung je Kernfunktion F1–F7 (Schwerpunkt `up` / `heal` / `service` / `monitor`) für leere Zustände, Grenzwerte, Nebenläufigkeit, Netzwerk-/Systemfehler und böswillige Eingaben |

**Konventionen**

- **RF-Fx-nn** = Randfall der Funktion Fx.
- Erwartetes Verhalten ist abnahmefähig (Exit-Code, CLI-Ausgabe, Dateizustand), ohne Lösungsvorgabe an die Architektur.
- CLI-Messages auf Englisch (Produktstandard).
- **ANNAHME RF-A0 (Exit-Codes):** `0` = Erfolg; `1` = Nutzer-/Validierungsfehler (Config, Args); `2` = System-/Runtime-Fehler (OS-Befehl, Rechte, Hardware); `3` = Teil-Erfolg (mind. ein Peer/Link fehlgeschlagen, Rest ok). Begründung: CLI-skriptierbar, gängig, Brief verlangt klare Meldung bei Admin-Bedarf.
- Node-Limit v1: 2–4 Nodes hart (Brief ANNAHME 11).
- Performance-Defaults (Brief ANNAHME 10): `status`/`topo` < 3 s; Monitor-Refresh 1–2 s; heal-Zyklus Default 30 s.

---

## F1 — Thunderbolt-Hardware-Info (`tb` / TB-Info)

| ID | Fall | Auslöser | Erwartetes Verhalten |
|---|---|---|---|
| RF-F1-01 | Kein Thunderbolt-Controller | `system_profiler`/`ioreg` liefert keine TB/USB4-Controller | Exit ≠ 0 oder Exit 0 mit explizitem „no Thunderbolt controller found“; kein Crash; leere Portliste klar gekennzeichnet. |
| RF-F1-02 | Controller vorhanden, 0 Ports/Receptacles | Parser findet Controller, aber keine Receptacles | Anzeige „0 ports“; kein erfundenes Mapping. |
| RF-F1-03 | Port ohne Link (Kabel ab) | Receptacle idle / no link | Port gelistet; Speed/Peer als „none“ / „down“; kein Fehler-Exit (Info-Befehl). |
| RF-F1-04 | Link up, Peer unbekannt | Link aktiv, Domain/Peer-ID fehlt | Port + Speed angezeigt; Peer-Feld „unknown“; Domain-UUID „—“ wenn nicht lesbar. |
| RF-F1-05 | Verhandelte Speed unter max | TB4-Port, Link nur 20 Gb/s o. ä. | Angezeigte Speed = verhandelte Link-Speed, nicht nur Port-Fähigkeit; beide Werte unterscheidbar, falls beides verfügbar. |
| RF-F1-06 | `system_profiler` fehlgeschlagen | Befehl fehlt, Timeout, Permission | Exit 2; Fehlermeldung nennt Quelle (`system_profiler`/`ioreg`); kein Stacktrace an den Operator. |
| RF-F1-07 | Unerwartetes/kaputtes Profiler-Output | Fixture mit Truncation/XML-Müll | Parse-Fehler → Exit 2 oder partiell geparste Felder mit Warnung; Prozess endet sauber. |
| RF-F1-08 | Nicht-Apple-Silicon / kein Mac mini | Intel-Mac oder anderes Modell | **ANNAHME RF-A1:** Warnung „unsupported platform“; read-only Info darf best-effort laufen; `up`/`heal` lehnen ab (Exit 1). Begründung: Brief Zielplattform Apple Silicon Mac mini. |
| RF-F1-09 | Mehrere TB-Controller | Ungewöhnliche Hardware | Alle Controller/Ports gelistet; keine stillschweigende Auswahl des „ersten“ ohne Kennzeichnung. |
| RF-F1-10 | Sehr langes Interface-/Port-Label | OS liefert ungewöhnlich lange Strings | Ausgabe bricht lesbar um oder kürzt mit Hinweis; kein Terminal-Korrupt. |

---

## F2 — Cluster-Config / `init` (TOML, feste TB-IPs, Node-Identität)

| ID | Fall | Auslöser | Erwartetes Verhalten |
|---|---|---|---|
| RF-F2-01 | Keine Config-Datei | Config-Pfad fehlt, Befehl braucht Config | Exit 1; Hinweis `maccluster init` bzw. Pfad; keine stillen Defaults, die Cluster-IPs setzen. |
| RF-F2-02 | Leere TOML | Datei existiert, 0 Bytes oder nur Kommentare | Exit 1; Validierungsfehler „empty/invalid config“. |
| RF-F2-03 | Ungültige TOML-Syntax | Fehlende Klammern, Bad Escape | Exit 1; Zeile/Position soweit möglich; Config unverändert. |
| RF-F2-04 | 0 Nodes | `nodes = []` oder fehlende Nodes | Exit 1; „at least 2 nodes required“. |
| RF-F2-05 | 1 Node | Nur Self in Config | Exit 1; Minimum 2. |
| RF-F2-06 | 2 Nodes (Minimum) | Gültige 2-Node-Config | Akzeptiert; alle abhängigen Befehle arbeiten mit 2 Peers (1 peer). |
| RF-F2-07 | 4 Nodes (Maximum) | Gültige 4-Node-Config | Akzeptiert. |
| RF-F2-08 | 5+ Nodes | Operator trägt 5 Nodes ein | Exit 1; „max 4 nodes in v1“; kein stilles Truncating. |
| RF-F2-09 | Doppelte IP | Zwei Nodes gleiche TB-IP | Exit 1; benennt Konflikt (IP + Node-IDs). |
| RF-F2-10 | Doppelte Hostname / HW-UUID | Identitätskollision | Exit 1; Konflikt klar benannt. |
| RF-F2-11 | IP außerhalb Subnetz | Node-IP nicht in Config-Subnetz | Exit 1; Validierung vor Schreiben/Anwenden. |
| RF-F2-12 | Ungültige IP / CIDR | `10.42.0.999`, `abc`, Subnetz `/33` | Exit 1; Feldname in Fehlermeldung. |
| RF-F2-13 | Self nicht bestimmbar | Hostname/HW-UUID matcht keinen Node | Exit 1 bei `up`/`heal`/`status`; Meldung „local node not in config“ + was verglichen wurde (Hostname, UUID — keine Secrets). |
| RF-F2-14 | Mehrere Nodes matchen Self | Zwei Einträge mit gleichem Hostname | Exit 1; Ambiguität, kein Raten. |
| RF-F2-15 | `init` überschreibt bestehende Config | Config existiert bereits | **ANNAHME RF-A2:** Abbruch mit Exit 1 außer explizitem `--force`; bei `--force` vorherige Datei bleibt als Backup (z. B. `.bak`) oder Operator wird gewarnt. Begründung: Config ist Wahrheit (Brief ANNAHME 13). |
| RF-F2-16 | Config nicht lesbar (Permissions) | Mode 000 / fremder Owner | Exit 2; „permission denied“ + Pfad. |
| RF-F2-17 | Config-Pfad ist Verzeichnis / Symlink-Loop | Falscher Pfadtyp | Exit 1/2; keine Endlosschleife. |
| RF-F2-18 | Extrem große Config | Multi-MB TOML | **ANNAHME RF-A3:** Ablehnen ab z. B. 1 MB; Exit 1 „config too large“. Begründung: 4 Nodes, kleines Volumen. |
| RF-F2-19 | Unbekannte TOML-Keys | Zusätzliche Felder | **ANNAHME RF-A4:** Unbekannte Keys → Warnung, kein harter Fail (Forward-Compat); Pflichtfelder fehlen → Exit 1. |
| RF-F2-20 | Interface-Name leer / ungültig | `interface = ""` oder Steuerzeichen | Exit 1; Validierung. |
| RF-F2-21 | Subnetz kollidiert mit bestehendem Routing | z. B. gleiches Subnetz wie LAN | `init`/`up` warnt; **ANNAHME RF-A5:** `up` bricht nicht automatisch ab, aber `doctor` markiert Konflikt; Operator entscheidet. Begründung: lokales Tool, kein automatisches Umnummerieren. |
| RF-F2-22 | Path-Traversal in Config-Pfad-CLI-Arg | `--config ../../etc/passwd` | Nur als Config-Datei öffnen; kein Schreiben außerhalb erlaubter Pfade bei `init`; keine Privilege-Eskalation. |
| RF-F2-23 | TOML mit eingebetteten Nullbytes / Binär | Böswillige Datei | Exit 1; kein Crash, kein unkontrolliertes Verhalten. |

---

## F3 — Bring-up (`up`): Thunderbolt Bridge + feste IP

| ID | Fall | Auslöser | Erwartetes Verhalten |
|---|---|---|---|
| RF-F3-01 | Erster `up` auf frischem Node | Bridge/IP noch nicht gesetzt | Bridge + feste IP gemäß Config; Exit 0; Status danach zeigt Self erreichbar (Loopback/TB-IP). |
| RF-F3-02 | Idempotenter `up` | Bridge/IP bereits korrekt | Exit 0; keine destruktive Rekonfiguration; Meldung „already configured“ o. ä. erlaubt. |
| RF-F3-03 | Bridge fehlt, IP verwaist | Interface weg, Adresse noch in Config-State | `up` stellt Bridge und IP wieder her; Exit 0 oder 3 mit klarer Liste der Schritte. |
| RF-F3-04 | IP bereits von anderem Interface belegt | Konflikt auf Host | Exit 2; benennt Interface/IP-Konflikt; ändert fremde Interfaces nicht stillschweigend. |
| RF-F3-05 | Keine Admin-Rechte | `up` ohne sudo, OS verweigert | Exit 2; klare Meldung „admin/sudo required“ (Brief G); kein Partial-State ohne Hinweis. |
| RF-F3-06 | `networksetup`/`ifconfig` schlägt fehl | OS-Befehl non-zero | Exit 2; stderr-Kernaussage weitergereicht (gekürzt); Rollback best-effort oder dokumentierter Partial-State. **ANNAHME RF-A6:** Bei Fehler nach Teilschritt: Exit 2 + welche Schritte ok/fehlgeschlagen; kein stilles „success“. |
| RF-F3-07 | TB-Kabel steckt nicht | Kein physikalischer Link | `up` darf lokale Bridge/IP trotzdem setzen (lokal); Exit 0 oder 3 mit Warnung „no TB link“; Peer-Erreichbarkeit nicht behauptet. |
| RF-F3-08 | Falsches Receptacle→Interface-Mapping | OS mappt anders als Doku | `up` nutzt erkannte Ifaces, nicht geratene; bei Unklarheit Exit 1/2 mit Diagnosehinweis (`tb`/`doctor`). |
| RF-F3-09 | Config Self-IP ≠ gewünschte | Operator ändert Config, alte IP noch aktiv | `up` bringt Config-IP; alte TB-Cluster-IP wird entfernt/ersetzt, nicht parallel belassen (kein Dual-IP-Chaos ohne Hinweis). |
| RF-F3-10 | Nebenläufig: zwei `up` parallel | Zwei Terminals gleichzeitig | **ANNAHME RF-A7:** Lock-Datei oder äquivalent; zweiter Prozess Exit 1/2 „another up/heal in progress“; kein korrupter Interface-State. |
| RF-F3-11 | Abbruch mitten im `up` (SIGINT) | Ctrl-C während OS-Aufruf | Prozess endet; Partial-State via `status`/`doctor` erkennbar; erneutes `up` ist sicher (idempotent/heilbar). |
| RF-F3-12 | Nicht-macOS / unsupported | Siehe RF-F1-08 | `up` lehnt ab Exit 1. |
| RF-F3-13 | Interface-Name aus Config existiert nicht | Tippfehler `bridge999` | Exit 2 nach Probe; Vorschlag erkannter TB-Ifaces in Meldung. |
| RF-F3-14 | MTU/Link-Flapping während `up` | Link flackert | `up` endet mit klarem Ergebnis; kein Hang > dokumentiertem Timeout. **ANNAHME RF-A8:** Einzelner OS-Schritt-Timeout z. B. 15 s. |
| RF-F3-15 | Böswillige Interface-Namen in Config | `; rm -rf /`, Command-Injection in Shell | Keine Shell-Interpolation ungefilterter Config-Werte; Exit 1 bei ungültigem Identifikator `[A-Za-z0-9_-]`. |

---

## F4 — Heal + LaunchAgent-Service (`heal`, `service`)

### F4a — `heal` (einmalig und Loop)

| ID | Fall | Auslöser | Erwartetes Verhalten |
|---|---|---|---|
| RF-F4-01 | Alles gesund | Bridge, IP, Links ok | `heal` einmalig: Exit 0; „healthy“ / keine Änderungen; Loop: idle bis nächster Tick. |
| RF-F4-02 | Bridge nach Reboot weg | LaunchAgent/`heal` nach Boot | Bridge+IP werden wiederhergestellt; messbar per `status` (Brief Erfolgskriterium). |
| RF-F4-03 | Nur IP fehlt | Bridge da, Adresse weg | Nur IP wird gesetzt; keine unnötige Bridge-Zerstörung. |
| RF-F4-04 | Fremde IP auf TB-Bridge | Manuell falsch gesetzt | Heal setzt Config-IP; dokumentiert Korrektur in Ausgabe (und optionalem Action-Log wenn aktiv). |
| RF-F4-05 | Peer down, lokal ok | Kabel raus / Peer aus | Heal ändert lokales Interface nicht „kaputt“; meldet Peer unreachable; Exit 0 (lokal healthy) oder 3 (cluster degraded). **ANNAHME RF-A9:** Lokal ok + Peer down → Exit 3 bei einmaligem heal mit Reachability-Check; Loop läuft weiter. |
| RF-F4-06 | Config fehlt während heal | Datei gelöscht | Exit 1; Service-Loop loggt Fehler und retry; kein Crash-Loop ohne Backoff. **ANNAHME RF-A10:** Bei wiederholtem Config-Fehler exponentielles/lineares Backoff bis Cap (z. B. max 5 min), Default-Zyklus 30 s. |
| RF-F4-07 | Keine Rechte im Loop | LaunchAgent ohne nötige Privilegien | Jeder Zyklus: klarer Fehler in Log; Agent bleibt alive; Exit-Code des Agent-Jobs ≠ silent success. |
| RF-F4-08 | Heal-Intervall 0 / negativ | CLI `--interval 0` oder Config | Exit 1 Validierung; Minimum **ANNAHME RF-A11:** ≥ 5 s. |
| RF-F4-09 | Heal-Intervall extrem groß | z. B. 86400 s | Erlaubt; Warnung optional. |
| RF-F4-10 | Zwei heal-Loops parallel | Manueller `heal --loop` + LaunchAgent | Lock wie RF-F3-10; nur einer führt mutierende Schritte aus; der andere wartet oder exit mit Hinweis. |
| RF-F4-11 | `heal` und `up` gleichzeitig | Parallel mutierend | Gleicher Lock-Bereich; serialisiert. |
| RF-F4-12 | SIGTERM an Loop | `service` stop / kill | Sauberes Beenden innerhalb weniger Sekunden; Lock freigeben. |
| RF-F4-13 | OS-Befehl hängt | `ifconfig` blockiert | Timeout (RF-A8); Zyklus als failed loggen; nächster Tick erneut. |
| RF-F4-14 | Action-Log voll / Disk full | Optional Log aktiv, Platte voll | Heal-Kernpfad scheitert nicht primär am Log; Warnung; **ANNAHME RF-A12:** Log-Write-Fehler sind non-fatal. |
| RF-F4-15 | Clock-Skew / Zeit sprunghaft | Systemzeit geändert | Timestamps in Snapshots monoton best-effort; kein Crash. |

### F4b — `service install` / `uninstall` / `status`

| ID | Fall | Auslöser | Erwartetes Verhalten |
|---|---|---|---|
| RF-F4-16 | Install ohne Admin wo nötig | LaunchAgent-Pfad erfordert Rechte | Exit 2; „admin required“; kein halbes Plist ohne Hinweis. |
| RF-F4-17 | Install idempotent | Service schon installiert | Exit 0; aktualisiert Plist bei Bedarf oder meldet already installed. |
| RF-F4-18 | Uninstall ohne Installation | Nichts installiert | Exit 0 oder 1 mit „not installed“ — **ANNAHME RF-A13:** Exit 0 idempotent uninstall. |
| RF-F4-19 | `launchctl` fehlgeschlagen | load/unload error | Exit 2; OS-Meldung; `service status` zeigt realen Zustand. |
| RF-F4-20 | Binary-Pfad nach Move | pipx-Upgrade, alter Plist-Pfad | `service status` meldet missing binary / not running; `service install` (re) schreibt korrekten Pfad. |
| RF-F4-21 | Status: installed but not running | Plist da, Prozess tot | `status` unterscheidet installed / running / healthy last-run. |
| RF-F4-22 | Status: running but config broken | Agent läuft, Config invalid | Status running=true; last error aus Log/State sichtbar. |
| RF-F4-23 | Doppel-Install verschiedene User | User- vs. system-Agent | **ANNAHME RF-A14:** Default User LaunchAgent (`~/Library/LaunchAgents`); Konflikte melden, nicht beide still installieren. |
| RF-F4-24 | Plist manuell korrupt | Operator editiert XML kaputt | `service status`/`install` erkennt und meldet; re-install überschreibt mit gültiger Vorlage. |
| RF-F4-25 | Uninstall während heal-Zyklus | Race | Unload stoppt Loop; Lock freigegeben; kein Zombie-Schreibzugriff auf Interfaces nach Unload-Timeout. |

---

## F5 — Live-CLI-Monitor (`monitor` / `status`)

| ID | Fall | Auslöser | Erwartetes Verhalten |
|---|---|---|---|
| RF-F5-01 | Leerer Cluster-Zustand | Config ok, noch nie `up` | Nodes aus Config gelistet; Self/peers als not configured / unreachable; kein Crash; Symbole+Text (nicht nur Farbe). |
| RF-F5-02 | Alle Nodes erreichbar | Happy Path | Alle reachability ok; Refresh 1–2 s; UI/Terminal bleibt bedienbar (Ctrl-C beendet). |
| RF-F5-03 | Ein Peer down | 1 von N unreachable | Degraded klar; welcher Node/IP; Aggregat nicht „all ok“. |
| RF-F5-04 | Alle Peers down | Nur Self | Self ok, peers down; Exit bei einmaligem `status`: 3 (ANNAHME RF-A9). |
| RF-F5-05 | Ping Timeout | Peer filtert ICMP / langsam | Timeout pro Peer; **ANNAHME RF-A15:** Ping-Timeout Default 1 s, parallel oder sequentiell mit Gesamtbudget &lt; 3 s für `status`. |
| RF-F5-06 | Ping nicht verfügbar | `ping` fehlt in PATH | Exit 2 oder Fallback-Hinweis; Reachability „unknown“, nicht still grün. |
| RF-F5-07 | SSH-Probe optional, Key fehlt | Config SSH an, Key/Agent fehlt | Probe skip oder fail mit „ssh unavailable“; lokaler Status bleibt nutzbar (Brief offener Punkt 3 → optional). |
| RF-F5-08 | SSH Host-Key-Änderung | Remote Key mismatch | Fehler „host key“; kein automatisches Überschreiben von known_hosts. |
| RF-F5-09 | Monitor ohne TTY | Pipe/CI | Plaintext ohne interaktive Clears; `--json` stabil parsebar wenn gesetzt. |
| RF-F5-10 | `rich` fehlt | Optional Dependency weg | Plaintext-Fallback; Exit 0 wenn Daten ok (Brief Kann/Soll). |
| RF-F5-11 | Terminal sehr schmal | WIDTH=40 | Kein Absturz; Zeilen wrap/truncate mit lesbarem Kernstatus. |
| RF-F5-12 | Farbblind / NO_COLOR | `NO_COLOR=1` oder dumb terminal | Status über Text/Symbole vollständig (Brief A11y). |
| RF-F5-13 | Refresh unter Last | system_profiler langsam | Refresh darf skippen/veraltet markieren; kein UI-Freeze ohne Timeout; Timestamp „as of“. |
| RF-F5-14 | Config ändert sich während Monitor | Operator speichert TOML live | **ANNAHME RF-A16:** Nächster Refresh lädt Config neu oder zeigt Hinweis „config changed, restart monitor“; kein Crash bei verschwundener Datei (dann Fehlerzeile). |
| RF-F5-15 | JSON-Output Schema-Bruch | `--json` bei Fehler | JSON mit `ok: false` / error-Feld; Exit ≠ 0; stdout bleibt valides JSON (stderr für Humans optional). **ANNAHME RF-A17.** |
| RF-F5-16 | Node-Hostname mit Unicode | `màc-mini-ü` | Korrekt anzeigen/escapen in JSON; kein Encode-Crash. |
| RF-F5-17 | Gleichzeitige monitor-Prozesse | Mehrere Terminals | Read-only: erlaubt; keine Locks gegen einander; keine mutierenden Side-Effects. |
| RF-F5-18 | SIGWINCH / Resize | Terminal-Größe ändert sich | Nächster Frame passt sich an; kein Crash. |
| RF-F5-19 | Extrem schneller Refresh-Arg | `--interval 0.01` | Clamp auf Minimum (z. B. 0.5–1 s) oder Exit 1; CPU nicht spin-loop. |
| RF-F5-20 | böswillige ANSI in Hostname aus Config | Hostname enthält Escape-Sequenzen | Sanitizing der Anzeige; keine Terminal-Injection. |

---

## F6 — Topologie-Map (`topo`)

| ID | Fall | Auslöser | Erwartetes Verhalten |
|---|---|---|---|
| RF-F6-01 | Keine Kabel | Alle Ports down | Map zeigt Nodes isoliert / no links; Exit 0 mit leerer Link-Liste (Info). |
| RF-F6-02 | Line-Topologie 2 Nodes | Ein Kabel | Eine Kante; Domain-UUID soweit bekannt. |
| RF-F6-03 | Mesh unvollständig (3/4 Kabel) | Teilverkabelung | Vorhandene Links; fehlende erwartbare Kanten als missing (wenn Config Nodes kennt). |
| RF-F6-04 | Domain-UUID nicht lesbar | OS liefert keine UUID | Links ohne UUID; „domain unknown“; kein Abbruch. |
| RF-F6-05 | Widerspruch Config vs. Auto-Detect | Config sagt Peer A an Port 1, Detect sieht B | Warnung inconsistency; beide Sichten anzeigen; Exit 3 optional. |
| RF-F6-06 | >4 physische Geräte am Bus | Fremdgerät | v1: nur Config-Nodes highlighten; Fremde als unknown peer listen, nicht crashen. |
| RF-F6-07 | Timeout der Hardware-Probe | Profiler langsam | Exit 2 oder partial topo nach Timeout &lt; 3 s Budget (Brief). |
| RF-F6-08 | Asymmetrische Sicht | Node1 sieht Link, Node2 nicht | Lokal korrekte Sicht; kein erzwungenes „global truth“ ohne Daten. |
| RF-F6-09 | JSON topo | `--json` | Stabile Struktur nodes/links; leere links = `[]`. |
| RF-F6-10 | Unerwartete Zeichen in Domain-UUID | Müll aus Parser | Validieren/anzeigen raw truncated; kein Crash. |

---

## F7 — Doctor / Diagnose und optionaler Bandwidth-Bench

| ID | Fall | Auslöser | Erwartetes Verhalten |
|---|---|---|---|
| RF-F7-01 | Frische Installation | Noch keine Config | Doctor listet Checks: config missing (fail), TB hardware (info), privileges (info); Exit ≠ 0. |
| RF-F7-02 | Alle Checks grün | Cluster healthy | Exit 0; Checkliste ok. |
| RF-F7-03 | Einzelner Check rot | z. B. IP fehlt | Exit ≠ 0; betroffener Check + empfohlener nächster Befehl (`up`/`heal`). |
| RF-F7-04 | `iperf3` nicht installiert | `bench` aufgerufen | Exit 1; „iperf3 not found“; Install-Hinweis; kein Pseudo-Benchmark. |
| RF-F7-05 | `iperf3` da, Peer down | Bench zu unreachable IP | Exit 2/3; Timeout; kein Hang. |
| RF-F7-06 | `iperf3` Server nicht gestartet | Nur Client | Klare Meldung; **ANNAHME RF-A18:** Bench startet nicht automatisch dauerhaft fremde Server-Prozesse ohne Flag; oder dokumentiertes `--server` lokal. |
| RF-F7-07 | Bench während heal | Parallel Last | Erlaubt; Ergebnisse können schwanken — Hinweis in Ausgabe. |
| RF-F7-08 | Doctor mit kaputter Config | TOML invalid | Config-Check fail zuerst; weitere Checks best-effort oder skip. |
| RF-F7-09 | Doctor ohne Root | Read-only Checks | Laufen ohne Root; Checks die Root brauchen → „skipped (needs admin)“ nicht als false green. |
| RF-F7-10 | Sehr langes Doctor-Output | Viele Warnungen | Scrollbar/Plain; Exit-Code spiegelt worst check. |
| RF-F7-11 | Bench-Target Injection | Ziel-IP `$(reboot)` / Hostname mit Spaces | Nur validierte IPs/Hostnames aus Config oder strikte CLI-Validierung; keine Shell. |
| RF-F7-12 | iperf3 Multi-Minute | Operator setzt extreme Duration | **ANNAHME RF-A19:** Cap z. B. 60 s Default max; darüber Exit 1. |

---

## Querschnitt — Nebenläufigkeit, Persistenz, Missbrauch

| ID | Fall | Auslöser | Erwartetes Verhalten |
|---|---|---|---|
| RF-X-01 | Gleichzeitige mutierende Befehle | `up` ∥ `heal` ∥ `service install` | Ein Mutex/Lock für mutierende Netz-Operationen; Lesende Befehle blockieren nicht unnötig lange. |
| RF-X-02 | Stale Lock | Prozess hard-kill, Lock-Datei bleibt | **ANNAHME RF-A20:** Lock mit PID + Timestamp; stale nach z. B. 60–120 s oder totem PID übernehmbar. |
| RF-X-03 | Config-Write-Race | Zwei `init --force` | Atomisches Replace (write temp + rename) oder Lock; keine halb geschriebenen TOML. |
| RF-X-04 | Disk full bei Config-Write | `init` speichert | Exit 2; alte Config bleibt intakt. |
| RF-X-05 | Home-Directory nicht schreibbar | LaunchAgent-Pfad | Exit 2; klare Meldung. |
| RF-X-06 | Environment `PATH` minimal | launchd schlankes ENV | Service-Plist setzt ausreichenden PATH oder absolute Binary-Pfade. |
| RF-X-07 | Unerwartete CLI-Args | Unbekannte Flags, zu viele Positionals | Exit 1; Usage-Hinweis. |
| RF-X-08 | Extrem lange CLI-Args | 100k-Zeichen-String | Exit 1; kein Speicher-Blowup. |
| RF-X-09 | Subprocess-ENV-Injection | Env vars steuern OS-Tools bösartig | Keine unsicheren Env an Subprozesse durchreichen, die Verhalten unerwartet ändern (soweit kontrollierbar). |
| RF-X-10 | Ausgabe enthält Secrets | SSH-Key-Pfade ok, Key-Material nicht | Niemals Private-Key-Inhalte loggen; Passwörter nicht in CLI. |
| RF-X-11 | Symlink-Angriff auf Config-Pfad | Config-Pfad zeigt auf sensible Datei, `init --force` | **ANNAHME RF-A21:** Schreiben nur wenn reguläre Datei oder explizit erlaubt; Follow-Symlink-Policy dokumentiert (prefer refuse overwrite via symlink). |
| RF-X-12 | Sleep/Wake | Mac aus dem Schlaf | Nächster heal-Tick stellt Interfaces wieder her; Monitor zeigt transient down dann recovery. |
| RF-X-13 | Thunderbolt-Hotplug während monitor | Kabel ein/aus | Nächster Refresh zeigt neuen Link-State; kein Crash. |
| RF-X-14 | Locale/UTF-8 | `LANG=C` vs. UTF-8 | CLI funktioniert; Hardware-Parser robust gegen Encoding. |
| RF-X-15 | Python &lt; 3.11 | Falsche Runtime | Klarer Fehler beim Start „Python 3.11+ required“. |

---

## Mapping Kernbefehle → kritische Randfälle (QA-Fokus)

| Befehl | Leerer Zustand | Grenze | Nebenläufigkeit | Netz/System | Missbrauch |
|---|---|---|---|---|---|
| `up` | RF-F3-01, RF-F2-01 | RF-F2-08, RF-F3-09 | RF-F3-10, RF-X-01 | RF-F3-05–07, RF-F3-14 | RF-F3-15 |
| `heal` | RF-F4-01–02 | RF-F4-08–09 | RF-F4-10–12 | RF-F4-05–07, RF-F4-13 | RF-F2-23 via Config |
| `service` | RF-F4-18, RF-F4-21 | RF-F4-20 | RF-F4-25 | RF-F4-16, RF-F4-19 | RF-F4-24 |
| `monitor`/`status` | RF-F5-01 | RF-F5-11, RF-F5-19 | RF-F5-17 | RF-F5-05–08, RF-F5-13 | RF-F5-20 |
| `tb` | RF-F1-01–02 | RF-F1-09–10 | — (read-only) | RF-F1-06–07 | — |
| `topo` | RF-F6-01 | RF-F6-06 | — | RF-F6-07 | RF-F6-10 |
| `doctor`/`bench` | RF-F7-01, RF-F7-04 | RF-F7-12 | RF-F7-07 | RF-F7-05–06 | RF-F7-11 |
| Config/`init` | RF-F2-01–02 | RF-F2-04–08, RF-F2-18 | RF-X-03 | RF-F2-16 | RF-F2-22–23 |

---

## Getroffene ANNAHMEN (über Brief hinaus)

| ID | Annahme | Begründung |
|---|---|---|
| RF-A0 | Exit-Codes 0/1/2/3 | Skriptierbarkeit |
| RF-A1 | Unsupported Platform: warn + block mutate | Zielplattform Brief |
| RF-A2 | `init` ohne `--force` überschreibt nicht; Backup bei Force | Config = Wahrheit |
| RF-A3 | Config-Max ~1 MB | Kleines Volumen |
| RF-A4 | Unbekannte TOML-Keys = Warnung | Forward-Compat |
| RF-A5 | Subnetz-Kollision = Warnung, kein Auto-Abbruch bei `up` | Operator-Tool |
| RF-A6 | Partial `up` → Exit 2 + Schrittliste | Kein silent success |
| RF-A7 | Mutex für `up`/`heal` | Interface-Korruption vermeiden |
| RF-A8 | OS-Schritt-Timeout ~15 s | Kein Hang |
| RF-A9 | Degraded cluster → Exit 3 | Unterscheidbar von hard fail |
| RF-A10 | Backoff bei Config-Fehler im Loop | Kein Crash-Spin |
| RF-A11 | Heal-Interval min 5 s | CPU/Log-Spam |
| RF-A12 | Log-Write non-fatal | Kernpfad Heal |
| RF-A13 | Uninstall idempotent Exit 0 | CLI-Ergonomie |
| RF-A14 | Default User LaunchAgent | Weniger Root |
| RF-A15 | Ping-Timeout 1 s, status &lt; 3 s | NFA Brief |
| RF-A16 | Config-Reload oder Hinweis im Monitor | Live-Edit |
| RF-A17 | `--json` Fehler = valides JSON + Exit ≠ 0 | Automation |
| RF-A18 | Bench startet nicht ungebeten Dauer-Server | Sicherheit/UX |
| RF-A19 | Bench Duration Cap 60 s | Missbrauch/DoS lokal |
| RF-A20 | Stale-Lock-Übernahme | Robustheit |
| RF-A21 | Symlink-Overwrite restriktiv | Path-Safety |

---

## Offene Punkte (menschliches Gate / Architektur)

| Nr. | Punkt | Bezug | Klärung |
|---|---|---|---|
| 1 | SSH-Probes Pflicht vs. optional | RF-F5-07, Brief OP-3 | ARCHITEKTUR |
| 2 | Subnetz-Default final (`10.42.0.0/24`?) | RF-F2-21, Brief OP-2 | ARCHITEKTUR |
| 3 | Exakte Exit-Code-Semantik 3 (degraded) verbindlich? | RF-A0/A9 | ARCHITEKTUR / Gate |
| 4 | LaunchAgent User vs. System (Privilegien für Bridge) | RF-A14, RF-F3-05 | ARCHITEKTUR — ggf. Root-Helper nötig |
| 5 | Ob `up` ohne Link Exit 0 oder 3 | RF-F3-07 | ARCHITEKTUR |
| 6 | Konkrete Hostnames/HW-UUIDs der 4 Minis | Abnahme-Szenario | IMPLEMENTIERUNG / ABNAHME |
