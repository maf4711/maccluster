# FAQ — Operator (Fabrik-Owner)

| Feld | Wert |
|---|---|
| Produkt | MacCluster |
| Version | 0.1.0 |
| Datum | 2026-08-01 |
| Zielgruppe | Interner Betrieb, Gates, Abnahme |

> Deutsch · unter `_fabrik/60-abnahme/faq/` — nicht Pflicht im öffentlichen Produkt-README.

## Was ist neu in 0.1.0

- Erstrelease CLI Thunderbolt-Cluster für 2–4 Apple Silicon Mac minis
- Vollausbau Wellen 1–6; pytest **102** grün
- User-LaunchAgent `com.maccluster.heal`, best-effort Heal
- Abnahme: freigeben **mit Auflagen** (Live-2-Node + Recovery)

## Fabrik-Stand

| | |
|---|---|
| Phase | ABNAHME (Gate 4 ausstehend Auftraggeber) |
| Gates | autopilot |
| Abnahmebericht | `_fabrik/60-abnahme/ABNAHMEBERICHT.md` |
| Szenario | `_fabrik/60-abnahme/ABNAHME-SZENARIO.md` |
| QA | `_fabrik/50-qa/TESTBERICHT.md` |

## Betrieb

### Wie starte ich das Produkt lokal?

```bash
cd projects/maccluster
python3 -m pip install -e ".[dev]"
maccluster --help
# Mutationen nur auf macOS arm64 Mini mit sudo:
sudo maccluster up
```

### Welche Secrets/Env brauche ich wirklich?

Keine Secrets. Optional: `MACCLUSTER_CONFIG`, `NO_COLOR`.  
`MACCLUSTER_SKIP_PLATFORM_GUARD=1` **nur Tests/CI**, nie Produktion.

### Deploy / TestFlight?

Nein. Lokales CLI; kein `/fabrik-deploy` / `/fabrik-apple` vorgesehen.

## FAQ intern

### Was ist Out-of-Scope geblieben?

GUI/Web, HTTP-API, Cloud, Linux/Windows, >4 Nodes, Inference/RDMA-Orchestrierung, HA-SLA, Kabelführungs-Empfehlung jenseits `topo`.

### Welche ANNAHMEN gelten noch?

- Subnetz-Default `10.42.0.0/24` (AD-1)
- SSH-Probes optional, Default aus (AD-2)
- Exit 0/1/2/3 (AD-3)
- User-LaunchAgent (AD-4)
- `up` ohne TB-Link → Exit 3, IP setzen (AD-5)
- Config-Pfad `~/.config/maccluster/cluster.toml` (AD-6)

### Change Request nach FERTIG?

Mini-Durchlauf ab ANALYSE im selben `projects/maccluster/` (`company/PROZESS.md`).

## Störungen

| Symptom | Maßnahme |
|---|---|
| CI rot auf Linux | `MACCLUSTER_SKIP_PLATFORM_GUARD=1` in Workflow prüfen |
| Abnahme „Peer down“ | Config Self-Match + `up` auf **beiden** Minis |
| Agent heilt Bridge nicht | dokumentiert: User-Domain ohne Root → `sudo heal` |
| Mapping Exit 2 | `bridge_interface` setzen; `docs/receptacle-mapping.md` |

## Verweise

- Produkt-FAQs: `docs/faq/operator.md`, `docs/faq/developer.md`
- Zero-Cost: `company/ZERO-COST.md` (Fabrik-Repo)
