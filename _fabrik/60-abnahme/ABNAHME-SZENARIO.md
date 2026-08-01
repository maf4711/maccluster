# Abnahme-Szenario — MacCluster 0.1.0

| Feld | Wert |
|---|---|
| Datum | 2026-08-01 |
| Zielgruppe | Auftraggeber / Operator |
| Dauer | ca. 30–60 min (2 Nodes); +15 min je weiterem Node |
| Hardware | 2–4 Apple Silicon Mac minis, Thunderbolt-verkabelt |
| Software | MacCluster 0.1.0, Python 3.11+, Admin-Rechte |

Geführter Probelauf. Jeder Schritt: **Befehl → Erwartung → Exit**.  
Bei Abweichung: Notiz + `maccluster doctor` / `maccluster --json status`.

---

## Vorbereitung

| # | Check |
|---|---|
| P1 | Minis eingeschaltet, gleiche macOS-Session (User für LaunchAgent) |
| P2 | Thunderbolt-Kabel physisch verbunden (mind. Kette oder Mesh für 2 Nodes) |
| P3 | Subnetz `10.42.0.0/24` kollidiert nicht mit bestehendem LAN (sonst in Config ändern) |
| P4 | Pro Mini: Hostname bekannt; optional HW-UUID via `system_profiler SPHardwareDataType` |

Bezeichner im Szenario:

| Rolle | Beispiel-Hostname | Beispiel-IP |
|---|---|---|
| Node A (Start) | `mac-mini-a` | `10.42.0.1` |
| Node B | `mac-mini-b` | `10.42.0.2` |
| Node C/D | optional | `.3` / `.4` |

---

## Schritt 1 — Installation (jeder Node)

```bash
# Im Produktverzeichnis (oder aus Wheel):
pipx install .
# Alternative: ./install.sh  oder  python3 -m pip install -e .
maccluster --version
maccluster --help
```

| Erwartung | Exit |
|---|---|
| Version `0.1.0`; Help listet `tb`, `init`, `up`, `heal`, `status`, `monitor`, `topo`, `doctor`, `bench`, `service` | 0 |
| Help erwähnt best-effort / not HA (Heal) | 0 |

**Abbruchkriterium:** Binary nicht im PATH → Install korrigieren, nicht fortfahren.

---

## Schritt 2 — Thunderbolt lesen (ohne Admin)

Auf **Node A**:

```bash
maccluster tb
```

| Erwartung | Exit |
|---|---|
| Ports/Receptacles gelistet; Fähigkeit/Speed oder unconnected; kein sudo-Prompt | 0 |
| Mit Kabel: Link/Peer-Hinweise möglich; ohne: `NO-LINK` / no peer klar (Text/Symbol) | 0 |

Optional: `maccluster --json tb | head` → parsebares JSON mit `schema_version`.

---

## Schritt 3 — Config anlegen

**Nur Node A** (oder je Node `init`, dann manuell angleichen):

```bash
maccluster init
# Datei: ~/.config/maccluster/cluster.toml
```

| Erwartung | Exit |
|---|---|
| Datei existiert; `schema_version = 1`; Subnetz Default `10.42.0.0/24`; ≥2 Node-Stubs | 0 |
| Self-Hostname und/oder HW-UUID des lokalen Hosts vorausgefüllt | 0 |

Config **editieren** (alle Nodes der Flotte):

1. `hostnames` und `hw_uuid` pro Node auf reale Werte
2. IPs eindeutig im Subnetz (`10.42.0.1` …)
3. `bridge_interface = "bridge0"` belassen oder Override laut `docs/receptacle-mapping.md`

**Dieselbe logische Config** auf Node B (C, D) kopieren:

```bash
# z. B. scp ~/.config/maccluster/cluster.toml node-b:~/.config/maccluster/
```

Validieren **auf jedem** Node:

```bash
maccluster config validate
maccluster config show
```

| Erwartung | Exit |
|---|---|
| validate: Self matched genau einmal | 0 |
| show: Name, Subnetz, alle Nodes | 0 |
| Falsche UUID/Hostname auf diesem Host | **2** (Self-Mismatch) — Config korrigieren |

Guardrail-Check (einmal):

```bash
maccluster init   # ohne --force, Datei existiert
```

| Erwartung | Exit |
|---|---|
| Datei unverändert; Meldung Overwrite verweigert | **2** |

---

## Schritt 4 — Bring-up (`up`)

**Auf jedem Member nacheinander** (lokal, mit Admin):

```bash
sudo maccluster up
```

| Situation | Erwartung | Exit |
|---|---|---|
| Rechte ok, TB-Link da, Bridge/IP gesetzt | Meldung Interface + IP | **0** |
| Rechte ok, **kein** TB-Link, Bridge/IP trotzdem gesetzt | Meldung enthält no TB link (o. ä.) | **3** |
| Ohne sudo / ohne Rechte | `admin/sudo required` | **1** |
| Zweites `up` bei korrektem State | already configured / idempotent | **0** (oder 3 wenn weiterhin no link) |

**Wichtig:** `up` ändert nur den **lokalen** Host — nie Peers remote.

Wi-Fi/`en0` Default-Route darf nicht umgebogen werden (Stichprobe: Routing vor/nach vergleichen).

---

## Schritt 5 — Status, Topo, Doctor

Auf Node A (nach up auf A und B):

```bash
maccluster status
maccluster topo
maccluster doctor
```

| Befehl | Erwartung | Exit |
|---|---|---|
| `status` alle Peers erreichbar | Nodes mit IP, UP, Timestamp; Self markiert | **0** |
| `status` ein Peer down | down markiert | **3** |
| `topo` | Map/Links; unmatched ausgewiesen; **keine** „plug cable from X to Y“-Empfehlung | 0 |
| `doctor` | Checks Config/Self/TB/Bridge/Peers; admin-only skipped ok | 0 / 3 / 1 je worst |

Optional JSON:

```bash
maccluster --json status
echo $?
```

---

## Schritt 6 — Live-Monitor

```bash
maccluster monitor
# optional: maccluster monitor --interval 2
# Ctrl+C
```

| Erwartung | Exit |
|---|---|
| Periodische Aktualisierung; Erreichbarkeit + TB-Link-Hinweis (nicht nur Ping) | läuft |
| Peer stecken ziehen → DOWN sichtbar (Text/Symbol, auch mit `NO_COLOR=1`) | läuft weiter |
| Ctrl+C sauber | **0** |

---

## Schritt 7 — Service (optional, Soll)

```bash
maccluster service install
maccluster service status
```

| Erwartung | Exit |
|---|---|
| Plist `~/Library/LaunchAgents/com.maccluster.heal.plist`; Label `com.maccluster.heal` | 0 |
| status: installed=true; running soweit ermittelbar; Intervall ~30 s | 0 |
| Erneutes install idempotent | 0 |

**Grenze:** User-LaunchAgent — ohne Root kann Bridge nach Reboot scheitern. Das ist spezifiziert (best-effort).

Optional KeepAlive:

```bash
# heal-PID des Agents ermitteln und beenden; innerhalb ~60 s erneut laufend?
```

Deinstall:

```bash
maccluster service uninstall
maccluster service status   # not installed; Exit 0 auch wenn schon weg
```

---

## Schritt 8 — Recovery (A-038, Auflage)

**Variante A — Bridge/IP manuell stören** (schneller als Reboot):

```bash
# Nur wenn Operator die Konsequenzen kennt — TB-Bridge lokal:
# Bridge/IP entfernen oder ifconfig zurücksetzen (Host-spezifisch)
sudo maccluster heal
maccluster status
```

**Variante B — Reboot** eines Members, nach Login:

```bash
# Agent geladen: warten ≤120 s  ODER:
sudo maccluster heal
maccluster status
```

| Erwartung | Exit |
|---|---|
| Bridge + Self-IP wieder gemäß Config (best-effort) | heal **0** bei Erfolg |
| Peers wieder erreichbar wenn Kabel/OS ok | status 0 oder 3 partial |

---

## Schritt 9 — Bench (optional)

```bash
# Auf Peer: iperf3 -s
maccluster bench 10.42.0.2
```

| Erwartung | Exit |
|---|---|
| iperf3 vorhanden + Ziel erreichbar → Durchsatz | 0 |
| iperf3 fehlt → „iperf3 not found“ + Hinweis | **1** |
| Rest-CLI (`status`, `doctor`) weiter nutzbar | — |

---

## Schritt 10 — Robustheit / Fehlerpfade (Stichprobe)

| Aktion | Erwartung | Exit |
|---|---|---|
| `maccluster status` ohne Config-Datei | Pfad genannt + Hinweis `init` | **2** |
| Config mit 1 oder 5 Nodes | Ablehnung „2–4“ / max 4 | **2** |
| `NO_COLOR=1 maccluster status` | Zustände ohne Farbe unterscheidbar | 0/3 |
| Offline WAN (Wi-Fi aus) | `tb`/`status`/`topo`/`doctor` mit lokalen Tools | funktionsfähig |

---

## Ergebnisprotokoll (Auftraggeber)

| Schritt | Node(s) | OK? | Exit / Notiz |
|---|---|---|---|
| 1 Install | alle | | |
| 2 tb | A | | |
| 3 init/validate | alle | | |
| 4 up | alle | | |
| 5 status/topo/doctor | A (+B) | | |
| 6 monitor | A | | |
| 7 service | A | | optional |
| 8 recovery | A | | **Auflage** |
| 9 bench | optional | | |
| 10 Fehlerpfade | A | | |

**Abnahme-Cluster bestätigt wenn:**

1. Mindestens **2 Nodes** mit `up` + gegenseitig erreichbar (`status` Exit 0), und  
2. **Recovery** (Schritt 8) einmal erfolgreich, und  
3. Keine kritischen Abweichungen von README/Exit-Code-Tabelle.

Danach Gate 4 in `_fabrik/state.json` freigeben.

---

## Verweise

- Produkt-README: `projects/maccluster/README.md`
- Abnahmebericht: [`ABNAHMEBERICHT.md`](./ABNAHMEBERICHT.md)
- Mapping: `docs/receptacle-mapping.md`
- Operator-FAQ: `docs/faq/operator.md`
