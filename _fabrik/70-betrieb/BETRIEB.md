# Betrieb — MacCluster (Fabrik-Inventar)

| Feld | Wert |
|---|---|
| Projekt | MacCluster (`maccluster`) |
| Phase | FERTIG (Pipeline) + laufender Betrieb |
| Stand | 2026-08-02 |
| Produktversion | **0.1.3** |
| GitHub | https://github.com/maf4711/maccluster |
| Releases | v0.1.0 … v0.1.3 |

Dieses Verzeichnis speichert **Betriebswissen und Session-Stand** in den Fabrik-Daten
unter `projects/maccluster/_fabrik/`. Es ersetzt nicht `~/.config/maccluster/` auf den Minis.

---

## 1. Was MacCluster tut (Kurz)

CLI für 2–4 Apple Silicon Mac minis über Thunderbolt:

- Config mit festen IPs (`10.42.0.0/24`)
- lokal Bridge/IP (`up`/`heal`), optional LaunchAgent
- Monitor: Nodes, TB-Links, TX/RX, doctor, topo
- **Kopiert/clont nicht still** auf Peers — Install/Config muss pro Node oder per Skript

---

## 2. Live-Flotte (studio-cluster)

| Node | Rolle | Hostnamen (Auszug) | IP | HW UUID | Stand 2026-08-02 |
|---|---|---|---|---|---|
| **node-a** | self (dieser Mac) | `CM-CFMQ2D029F` (+ Aliase) | `10.42.0.1` | `409C591A-9803-5203-B8C9-E72E73A3EF6E` | pipx 0.1.3, Bridge-IP gesetzt, Heal-Service **running** |
| **node-b** | peer TB | `CM-KWFVR7JGW3` | `10.42.0.2` | Platzhalter `…000B2` bis Peer-Init | L2/ARP + oft TCP:22; **SSH-Login blockiert**; MacCluster-CLI remote **nicht** installiert |

Config-Snapshot: [`cluster.toml.snapshot`](./cluster.toml.snapshot)  
Live-CLI-Ausgabe: [`LIVE-SNAPSHOT.md`](./LIVE-SNAPSHOT.md)

---

## 3. Pfade auf node-a (dieser Mac)

| Was | Pfad |
|---|---|
| CLI (pipx) | `~/.local/bin/maccluster` |
| Config | `~/.config/maccluster/cluster.toml` |
| LaunchAgent | `~/Library/LaunchAgents/com.maccluster.heal.plist` |
| Traffic-Cache | `~/Library/Caches/maccluster/traffic_sample.json` |
| Audit (default aus) | `~/.local/state/maccluster/actions.log` |
| Produkt-Repo | `~/Developer/fabrik/projects/maccluster` |
| Grok-Skill (User) | `~/.grok/skills/maccluster-status/` |
| Skill-Kopie in Fabrik | `_fabrik/skills/maccluster-status/` |

---

## 4. GitHub / Download

| Artefakt | URL |
|---|---|
| Repo | https://github.com/maf4711/maccluster |
| Install one-liner | `curl -fsSL https://raw.githubusercontent.com/maf4711/maccluster/main/install.sh \| bash` |
| raw install.sh | https://raw.githubusercontent.com/maf4711/maccluster/main/install.sh |
| ZIP main | https://github.com/maf4711/maccluster/archive/refs/heads/main.zip |
| Install-Doku | https://github.com/maf4711/maccluster/blob/main/docs/INSTALL.md |
| Peer-SSH-Hilfe | https://github.com/maf4711/maccluster/blob/main/docs/PEER-SSH.md |

---

## 5. Skill: Historie + Monitor

- **Slash:** `/maccluster-status` · `/maccluster`
- **Report:** `bash ~/.grok/skills/maccluster-status/scripts/maccluster-report.sh`
- Zeigt: doctor, status/traffic, tb, topo, service, config, git, CHANGELOG, Releases

---

## 6. Was *nicht* automatisch passiert

| Erwartung | Realität |
|---|---|
| Auto-Clone auf Peer | **nein** — nur `install.sh` / manuell / `remote-install.sh` bei SSH |
| Auto-Copy `cluster.toml` | nur mit `remote-install.sh --copy-config` **wenn SSH geht** |
| Action-Audit jeder `up` | default **aus** (kein `actions.log` bisher) |

---

## 7. Offene Betriebs-Punkte

1. **Peer node-b:** SSH `Connection closed` nach Passwort → Key nicht installiert; CLI auf Peer per AirDrop/Console (siehe `docs/PEER-SSH.md`)
2. **node-b hw_uuid** Platzhalter — nach `maccluster init` auf Peer ersetzen
3. **node-c/d** nicht in Live-Config (nur 2-Node-Studio)
4. Optional: Audit-Log default on

---

## 8. Verwandte Fabrik-Artefakte

| Phase | Inhalt |
|---|---|
| `00-intake/BRIEF.md` | Auftrag |
| `10-analyse/*` | Anforderungen, Stories, NFA |
| `20-architektur/*` | ARCHITEKTUR, ADRs, STACK |
| `30-planung/*` | Wellen, Backlog |
| `40-implementierung/PROTOKOLL.md` | Umsetzungsprotokoll |
| `50-qa/TESTBERICHT.md` | QA |
| `60-abnahme/*` | Abnahmebericht, Szenario |
| `70-betrieb/*` | **dieser Betriebsordner** |
| `skills/maccluster-status/` | archivierte Skill-Kopie |
