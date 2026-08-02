# Session-Chronik MacCluster (Fabrik)

Dokumentiert die Arbeit in der Fabrik-/Build-Session bis 2026-08-02.
Ergänzt Pipeline-Phasen ANALYSE…ABNAHME.

---

## 2026-08-01 — Intake & Pipeline

| Schritt | Ergebnis |
|---|---|
| Intake `/fabrik-auftrag` | Brief MacCluster, Vollausbau, Autopilot, Python CLI |
| ANALYSE | ANFORDERUNGEN A-001…, Stories, NFA, Risiken, Randfälle |
| ARCHITEKTUR | Hybrid pragmatisch+agentenfreundlich, ADR-0001…0007 |
| PLANUNG | 6 Wellen, 27 Stories |
| IMPLEMENTIERUNG | Volles Package `src/maccluster`, pytest grün |
| QUALITAET | TESTBERICHT, erweiterte Tests |
| ABNAHME | freigegeben → phase **FERTIG** |

Produkt initial: CLI-Befehle tb/init/config/up/heal/status/monitor/topo/doctor/bench/service.

---

## 2026-08-01…02 — Betrieb & Releases

| Schritt | Ergebnis |
|---|---|
| GitHub Repo | https://github.com/maf4711/maccluster public |
| install.sh one-liner | raw.githubusercontent.com |
| v0.1.0 | Initial release |
| Live node-a | pipx, config, `sudo up` → `10.42.0.1`, LaunchAgent heal |
| v0.1.1 | Live TX/RX traffic auf status/monitor |
| v0.1.2 | Ping `-S self-IP`, TCP:22 Fallback, remote-install.sh |
| v0.1.3 | Shared `health/reach.py`, Peer-Link 40G Anzeige |
| Skill | `maccluster-status` (User `~/.grok/skills/` + Kopie hier) |
| Peer SSH | `ssh-copy-id` → Connection closed after password; remote-install blockiert |
| Peer-Bootstrap | `scripts/peer-bootstrap-local.sh`, `docs/PEER-SSH.md`, Desktop-Dateien für AirDrop |

---

## Explizite „Copy/Clone“-Regel (User-Klarstellung)

- MacCluster **clont nicht** und **kopiert nicht still** auf andere Minis.
- `install.sh` installiert nur **auf dem Mac, auf dem er läuft**.
- `remote-install.sh --copy-config` kopiert Config **nur bei funktionierendem SSH**.
- Ohne SSH: AirDrop `cluster.toml` + Peer-Terminal (siehe PEER-SSH.md).

---

## Releases (GitHub)

| Tag | Thema |
|---|---|
| v0.1.0 | Initial CLI |
| v0.1.1 | Traffic rates |
| v0.1.2 | Reachability + remote-install |
| v0.1.3 | Unified reach + peer TB link display |

---

## Nächster Betriebs-Schritt

1. Auf node-b (Bildschirm): Install + gleiche `cluster.toml` + `sudo maccluster up`
2. Optional: Remote Login fixen, dann `ssh-copy-id` + `remote-install.sh`
3. node-b echte `hw_uuid` in Config auf beiden Seiten eintragen
