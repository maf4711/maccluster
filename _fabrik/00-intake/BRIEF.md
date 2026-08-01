# Projektauftrag (Brief)

| Feld | Wert |
|---|---|
| Projektname | MacCluster |
| Slug | maccluster |
| Datum | 2026-08-01 |
| Auftraggeber | Produktmanagement (Intake) |

> **Hinweis:** Mit `[Gründlich]` markierte Felder stammen aus Vertiefungsfragen. Im Kompakt-Modus sind sie mit „— (Kompakt-Modus: Fabrik entscheidet)" bzw. dem konkreten Default unter ANNAHMEN gefüllt.

---

## A — Vision & Ziel

**Produktbeschreibung (Freitext):**
CLI-Werkzeug für bis zu vier Apple Mac minis, die über Thunderbolt-Kabel dauerhaft als Netzwerk-Cluster betrieben werden. Das Produkt läuft symmetrisch auf jedem Cluster-Member (kein dedizierter Leader nötig). Es erkennt Thunderbolt-/USB4-Hardware (Port-Fähigkeit, verhandelte Link-Geschwindigkeit, angeschlossene Peers), baut den Thunderbolt-Bridge-Mesh mit festen IPs auf (Bring-up), hält den Cluster über Heal und optionalen LaunchAgent-Service online und bietet einen reinen Terminal-Monitor (Status, Topologie, Erreichbarkeit, Diagnose). Zielgruppe: Operator, der mehrere Mac minis lokal per Thunderbolt vernetzt und den Zustand jederzeit im Terminal prüfen will.

**Produktname:** MacCluster

**Ziel / Nutzenversprechen:**
Ein Befehlssatz (`maccluster`), mit dem der Operator den Thunderbolt-Cluster initialisiert, dauerhaft erreichbar hält und live überwacht — auf jedem Member identisch installierbar.

**Erfolgskriterium:** — (Kompakt-Modus: Fabrik entscheidet; siehe ANNAHMEN)

**Referenzen & Abgrenzung:** — (Kompakt-Modus: Fabrik entscheidet; siehe ANNAHMEN)

## B — Nutzer & Rollen

**Nutzerrollen:**
- Operator (einzige Rolle) — alle CLI-Befehle; Nutzung unter dem lokalen macOS-Benutzer

**Primäre Zielgruppe:** Operator von 2–4 Mac minis mit Thunderbolt-Verkabelung

**Rollen/Rechte-Matrix (Rolle × Funktion):** `[Gründlich]`
| Rolle | Config lesen/schreiben | up/heal | service install | monitor/status/topo | doctor/bench |
|---|---|---|---|---|---|
| Operator | ja | ja | ja (benötigt ggf. Admin-Rechte für LaunchAgent) | ja | ja |

**Nutzerverwaltung:** — (Kompakt-Modus: Fabrik entscheidet; siehe ANNAHMEN)

## C — Funktionsumfang (MoSCoW)

**Kernfunktionen:**
- F1 Thunderbolt-Hardware-Info (Version/Fähigkeit, Link-Speed, Ports/Receptacles)
- F2 Cluster-Config (TOML, feste TB-IPs, Node-Identität über Hostname/HW-UUID)
- F3 Bring-up (`up`): Thunderbolt Bridge + feste IP pro Node
- F4 Heal + LaunchAgent-Service („immer online“)
- F5 Live-CLI-Monitor (Nodes, Links, Erreichbarkeit)
- F6 Topologie-Map (Auto-Detect Domain-UUID / Kabel-Map)
- F7 Doctor/Diagnose und optionaler Bandwidth-Bench (wenn `iperf3` vorhanden)

| Priorität | Funktionen |
|---|---|
| Muss | F1 TB-Info · F2 Config/init · F3 up · F4 heal (+ einmalig) · F5 monitor · F6 topo · F7 doctor (Basis) |
| Soll | F4 service install/uninstall/status (LaunchAgent-Loop) · optionales JSON-Output · F7 bench wenn iperf3 |
| Kann | erweiterte Historie/Log-Rotation · farbige Rich-TUI falls Dependency erlaubt |

**Projektmodus (MVP / Vollausbau):** Vollausbau (Muss + Soll in einem Durchlauf)

**Ausbaustufen (spätere Erweiterungen):** — keine (Vollausbau)

**Leuchtturm-Funktion:** `[Gründlich]` Live-Monitor + Auto-Detect-Topologie auf jedem Member

**Explizit Out-of-Scope (wird NICHT gebaut):** *(L-05 bestätigt 2026-08-01)*
- Grafische Oberfläche / Web-UI / Desktop-App
- Öffentliche HTTP-API für Dritte
- Cloud-Deploy, Docker-Pflicht, Multi-Tenant
- Linux/Windows als Zielplattform (nur macOS Apple Silicon Mac mini)
- exo / LLM-Inference-Orchestrierung / RDMA-Enablement (Recovery-OS)
- Zentrale Datenbank, Multi-User-Login, OAuth
- Automatische physische Kabelführungs-Empfehlung jenseits von `topo`
- Live-Trading-Guards und meradOS-/Fremdprodukt-Anbindungen (Neutralität)

## D — Daten & Domäne

**Persistente Daten:** Ja, lokal beim Nutzer — Config-Datei und optional lokale Status-/Log-Dateien; keine zentrale DB

**Kernentitäten / Domänenobjekte:**
- ClusterConfig (Name, Subnetz, Interface, Nodes)
- Node (id, hostnames, ip, hw_uuid, role self/peer)
- ThunderboltPort / ThunderboltLink (receptacle, iface, speed, domain_uuid, peer)
- HealthSnapshot (node reachability, ping, link status, timestamp)
- ServiceState (LaunchAgent installed/running)

**Datenherkunft:** — (Kompakt-Modus: Fabrik entscheidet; siehe ANNAHMEN)

**Migration:** — (Kompakt-Modus: Fabrik entscheidet; siehe ANNAHMEN)

**Datenvolumen:** — (Kompakt-Modus: Fabrik entscheidet; siehe ANNAHMEN)

## E — Schnittstellen & Integrationen

**Externe Integrationen / APIs:**
Keine Drittsystem-APIs. Nutzung von macOS-Bordmitteln und optionalen lokalen Tools:
| System | Zweck | Richtung |
|---|---|---|
| system_profiler / ioreg | TB-Hardware | lokal lesen |
| ifconfig / networksetup | Bridge, IPs, Interfaces | lokal lesen/schreiben |
| ping | Peer-Erreichbarkeit | lokal |
| launchctl | Heal-Service | lokal |
| iperf3 (optional) | Bandwidth-Bench | lokal, wenn installiert |
| SSH (optional, Config) | Peer-Probe von remote | ausgehend, wenn Keys vorhanden |

**Eigene API:** Nein, keine öffentliche API (CLI-Ausgabe Text; optionales `--json` intern erlaubt)

**Import / Export:** Config TOML import/export (Datei); Status optional JSON-Dump

## F — Nicht-funktionale Anforderungen

**Performance / Antwortzeiten:** — (Kompakt-Modus: Fabrik entscheidet; siehe ANNAHMEN)

**Skalierung / Lastprofil:** — (Kompakt-Modus: Fabrik entscheidet; siehe ANNAHMEN)

**Verfügbarkeit / Zuverlässigkeit:** — (Kompakt-Modus: Fabrik entscheidet; siehe ANNAHMEN)

**Backup & Datenverlust:** — (Kompakt-Modus: Fabrik entscheidet; siehe ANNAHMEN)

## G — Plattform & Technik

**Plattform:** CLI-Werkzeug auf macOS (Apple Silicon Mac mini; Thunderbolt Bridge)

**Stack-Vorgabe:** Python-Ökosystem (Python 3.11+, stdlib primär; optional `rich` für Monitor)

**Datenbank:** — (Kompakt-Modus: Fabrik entscheidet; siehe ANNAHMEN) — keine DB; Datei-Config

**Browser-Support:** — entfällt (kein Web)

**Repository-Sprache:** Englisch (fester Fabrik-Standard)

**Technische Randbedingungen:**
- Läuft ohne Root für read-only Befehle (`status`, `monitor`, `topo`, `tb`, `doctor` soweit möglich)
- `up` / `heal` / `service install` dürfen Admin/sudo benötigen und müssen das klar melden
- Symmetrisch: dieselbe Installation und Config-Struktur auf jedem Member
- Receptacle→Interface-Mapping (Apple Silicon Mac mini): dokumentieren und testbar halten
- Offline-fähig: keine Cloud-Abhängigkeit

## H — Sicherheit & Compliance

**Authentifizierung:** Kein Login — OS-Benutzer + optionale SSH-Keys zu Peers

**Personenbezogene Daten & DSGVO:** Keine personenbezogenen Daten (technische Host-/Netzdaten)

**Verschlüsselung:** — (Kompakt-Modus: Fabrik entscheidet; siehe ANNAHMEN)

**Audit-Log:** — (Kompakt-Modus: Fabrik entscheidet; siehe ANNAHMEN)

**Compliance-Vorgaben:** Keine besonderen; lokale Operator-Tooling-Standards

## I — UX & Design

**Designvorgaben / Stil:** — (Kompakt-Modus: Fabrik entscheidet; siehe ANNAHMEN) — nüchternes Terminal-UI

**UI-Sprache:** Englisch für CLI-Messages und README (Fabrik-Produktstandard); kurze DE-Hinweise nur in Fabrik-Artefakten

**Barrierefreiheit:** — (Kompakt-Modus: Fabrik entscheidet; siehe ANNAHMEN) — lesbare Plaintext-Ausgabe ohne reine Farbabhängigkeit

**Farbschema:** — (Kompakt-Modus: Fabrik entscheidet; siehe ANNAHMEN)

## J — Betrieb & Deployment

**Zielumgebung:** Lokal auf jedem Cluster-Member (macOS)

**Deployment / Auslieferung:** `pipx install` / `pip install -e .` / einfaches `install.sh`; LaunchAgent via `maccluster service install`

**CI:** — (Kompakt-Modus: Fabrik entscheidet; siehe ANNAHMEN)

**Umgebungen:** — (Kompakt-Modus: Fabrik entscheidet; siehe ANNAHMEN) — dev + local-prod auf den Minis

**Betriebsverantwortung / Monitoring:** Operator; eingebauter `monitor` + `heal --loop` als Betriebs-Monitoring

## K — Qualität & Abnahme

**Abnahmekriterien (Definition of Done):**
Alle Muss-Anforderungen umgesetzt und per Testbericht (`50-qa/TESTBERICHT.md`) nachgewiesen. Zusätzlich: README mit Install, Config-Beispiel für 4 Nodes, und Befehlsübersicht.

**Testtiefe / Qualitätsanspruch:** — (Kompakt-Modus: Fabrik entscheidet; siehe ANNAHMEN)

**Abnahme-Artefakte:** — (Kompakt-Modus: Fabrik entscheidet; siehe ANNAHMEN)

## L — Rahmen & Modus

**Gates-Modus:** Autopilot — Fabrik arbeitet durch; nur die finale Abnahme liegt beim Auftraggeber

**Wizard-Tiefe:** Kompakt

**Zeit- / Kostenrahmen:** Normal — Standard-Gründlichkeit

---

## ANNAHMEN

Jede Antwort „Fabrik entscheidet“ bzw. Kompakt-Lücke wird hier mit dem konkret gewählten Default dokumentiert.

| Nr. | Sektion | Frage | Gewählter Default der Fabrik | Begründung |
|---|---|---|---|---|
| 1 | A | A-03 Erfolgskriterium | Innerhalb von 3 Monaten: 4 Nodes per Config erreichbar; `monitor` zeigt korrekte TB-Links; Heal stellt Bridge/IP nach Reboot wieder her | Messbar und deckungsgleich mit „immer clustern“ |
| 2 | A | A-04 Referenzen | Abgrenzung zu generischen Cluster-Managern und zu Inference-Meshes; Vorbild: klassische Node-Health-CLIs + macOS Thunderbolt Bridge | Kein Copy; klärt Scope |
| 3 | B | B-03 Nutzerverwaltung | Keine App-Nutzerverwaltung; macOS-Accounts + optionale SSH-Keys | Kein Login (H-01) |
| 4 | B | B-04 Gast-Zugriff | Nein | CLI lokal, kein öffentlicher Zugang |
| 5 | C | C-03 MoSCoW | Siehe Tabelle Muss/Soll/Kann oben | Vollausbau sinnvoll geschnitten |
| 6 | D | D-02 Entitäten | ClusterConfig, Node, ThunderboltLink, HealthSnapshot, ServiceState | Aus A-01 abgeleitet |
| 7 | D | D-03 Herkunft | Operator pflegt Config; Live-Daten aus OS-Probes | Lokal-first |
| 8 | D | D-04 Migration | Nein, Start leer | Neues Produkt |
| 9 | D | D-05 Volumen | Klein (Config + kurze Status-Logs) | 4 Nodes |
| 10 | F | F-01 Performance | `status`/`topo` < 3 s; Monitor-Refresh 1–2 s; heal-Zyklus konfigurierbar (Default 30 s) | CLI-tauglich |
| 11 | F | F-02 Skalierung | 2–4 Nodes (hart dokumentiert; >4 nicht Ziel v1) | Auftrag „4 Mac mini“ |
| 12 | F | F-03 Verfügbarkeit | Best-effort Heal; kein HA-Garantieversprechen jenseits LaunchAgent-Restart | Operator-Tool |
| 13 | F | F-04 Backup | Config ist die Wahrheit; Nutzer versioniert `cluster.toml` selbst (z. B. Dotfiles) | Keine Server-DB |
| 14 | G | G-04 Datenbank | Keine — TOML/JSON-Dateien | D-01 lokal |
| 15 | H | H-04 Verschlüsselung | Keine extra App-Verschlüsselung; SSH für Remote-Probes nutzt bestehende Keys | Kein Login/keine PII |
| 16 | H | H-05 Audit-Log | Optionales lokales Append-Log der heal/up-Aktionen (Default aus) | Diagnose ohne Pflicht |
| 17 | I | I-01 Design | Nüchtern-funktionales Terminal; optionales rich; Plaintext-Fallback | CLI-only |
| 18 | I | I-03 Barrierefreiheit | Keine reine Farbkodierung kritischer Zustände (Symbole + Text) | Terminal-A11y basis |
| 19 | J | J-02 CI | GitHub Actions: lint + unit tests bei Push | Fabrik-Standard |
| 20 | J | J-03 Umgebungen | local only (kein staging/prod-Cloud) | Zielumgebung lokal |
| 21 | K | K-02 Testtiefe | Standard: Unit-Tests Kernlogik (Config, TB-Parse, Topo-Match) + Integrationstests mit Fixtures (system_profiler samples) | Kein Live-4-Node-Zwang in CI |
| 22 | K | K-03 Abnahme-Artefakte | TESTBERICHT.md, README, Beispiel-`cluster.toml`, kurzes Abnahme-Szenario in 60-abnahme | DoD |
| 23 | L | L-03 Zeitrahmen | Normaler Durchlauf | Kostenrahmen Normal |

## OFFENE PUNKTE

| Nr. | Punkt | Auswirkung | Klärung bis Phase |
|---|---|---|---|
| 1 | Konkrete Node-Hostnames und HW-UUIDs der 4 Minis | `init`-Beispiel und Abnahme-Szenario | IMPLEMENTIERUNG / ABNAHME |
| 2 | Subnetz-Wahl final (Vorschlag `10.42.0.0/24`) | Config-Default | ARCHITEKTUR |
| 3 | Ob SSH-Probes Pflicht oder optional sind | Monitor-Vollständigkeit ohne SSH | ARCHITEKTUR |

## MODUS

| Einstellung | Wert |
|---|---|
| Gates | autopilot |
| Wizard-Tiefe | Kompakt |
| Projektmodus | Vollausbau |
| Kostenrahmen | Normal |
