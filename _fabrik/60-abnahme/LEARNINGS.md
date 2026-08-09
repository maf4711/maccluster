# Learnings — MacCluster

| Feld | Wert |
|---|---|
| Projekt | MacCluster (`maccluster`) |
| Datum | 2026-08-09 |
| Quellen | ARCHITEKTUR, ABNAHMEBERICHT, Betrieb (retrospektiv) |

## 1. Genutzte Einträge aus `company/wissen/`

| Eintrag | Wo zitiert | Bewertung |
|---|---|---|
| `company/wissen/muster/distribution.md` | ARCHITEKTUR.md | **retrospektiv bestätigt** |
| `company/wissen/muster/agenten-orchestrierung.md` | ARCHITEKTUR.md | **retrospektiv bestätigt** |
| `company/wissen/ERKENNTNISSE.md` | ARCHITEKTUR.md | **retrospektiv bestätigt** |

**Kontext:** Produkt war vor der live geschalteten Wissensbasis (2026-08-08) fertig.
Zitate und dieses LEARNINGS wurden 2026-08-09 nachgezogen, damit der Kreislauf messbar
und die Lektionen kuratierbar sind — nicht als Fake-Pipeline-Lauf.

## 2. Vorschläge für die Wissensbasis

### L-NEU-MC-001 — Heal/Watchdog mit Hysterese, nicht flapping Restarts

- **Lektion:** Cluster-Ops, launchd, produktive Inbetriebnahme in 6 Wellen.
- **Beleg:** `maccluster` FERTIG-Betrieb.
- **Check:** Siehe Produkt-README und ABNAHMEBERICHT.

## 3. Kuratierungshinweis

Übernahme in `company/wissen/` nur kuratiert, wenn die Lektion noch nicht in
ERKENNTNISSE/muster steht. Kein automatischer Schreibzugriff.
