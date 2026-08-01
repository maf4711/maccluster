# WELLENPLAN — MacCluster

| Feld | Wert |
|---|---|
| Projekt | MacCluster (`maccluster`) |
| Phase | 3 PLANUNG |
| Stand | 2026-08-01 |
| Artefakte | `BACKLOG.md` · `wellen.json` · dieses Dokument |
| Umfang | **6 Wellen**, **27 Arbeitspakete** (US-000 + US-001…US-026) |
| Grundlage | `10-analyse/*`, `20-architektur/ARCHITEKTUR.md`, `STACK.md`, ADR-0001…0007 |

Verbindliche Dateibesitze und Akzeptanzkriterien: `BACKLOG.md`.  
Maschinenlesbar: `wellen.json` (Format: `wellen[].nr` + `stories[{id,titel,dateien,abhaengigkeiten}]`).

---

## 1. Überblick

| Welle | Stories | Inhalt | Parallelität |
|---|---|---|---|
| **1** | US-000 | Package, hatchling, argparse-CLI, Ports, ProcessRunner, Domain-Stubs, CI, `make verify` | 1 |
| **2** | US-003, US-002, US-010, US-026 | Config load/validate/paths, init+FS, Config-Fehler-Tests, Beispiel-TOML | 2–3 |
| **3** | US-012, US-001 | Plain-Render/Symbols; TB-Probes + Mapping + `tb` | 2 |
| **4** | US-004, US-005, US-011 | Network apply, Lock, Heal-Plan, `up`, `heal`, Privilege-Tests | sequentiell |
| **5** | US-006, US-008, US-017, US-007, US-020 | status, topo, JSON, monitor, Partial-Mesh-Tests | 3–4 |
| **6** | 12 Stories | Doctor, heal-loop, LaunchAgent, bench, SSH, Kann, Install/Offline | 6–8 |

**Kritischer Pfad:** 1 → 2 → 3 → 4 → 5 → 6.

**Leuchttürme:** Monitor (US-007) + Topo (US-008) in W5; Betriebsfundament up/heal in W4.

| Welle | IDs | # |
|---|---|---|
| 1 | US-000 | 1 |
| 2 | US-002, US-003, US-010, US-026 | 4 |
| 3 | US-001, US-012 | 2 |
| 4 | US-004, US-005, US-011 | 3 |
| 5 | US-006, US-007, US-008, US-017, US-020 | 5 |
| 6 | US-009, US-013–016, US-018–019, US-021–025 | 12 |
| **Summe** | | **27** |

---

## 2. Begründung des Schnitts

### 2.1 Welle 1 — Gerüst

Prozess: Welle 1 = Build-Setup, Basisstruktur, CI (QUALITAET G1–G5).

US-000 liefert installierbares Package, vollständigen argparse-Baum (alle Subcommands), Exit 0/1/2/3, Ports, ProcessRunner (A-044/A-045), Domain-Dataclasses, Platform-Guard-API-Stub, Smoke-Tests.

**`parser.py` bleibt bei US-000** — kein pro-Welle-CLI-Wire-Hotspot. Fach-Stories legen nur `commands/*` und `services/*` an.

### 2.2 Welle 2 — Config vor allem

Config ist Soll-Wahrheit. US-003 (load/validate/identity/platform) und US-002 (init/dump/filesystem) parallel über Typvertrag Domain-Models. US-010 = Fehlerpfad-Tests; US-026 = Beispiel + Portabilität.

### 2.3 Welle 3 — Hardware lesen + Darstellung

US-012 liefert Plaintext-Symbole (A-021-Basis). US-001 liefert dual-source TB-Parser, Mapping pure + Fixtures (A-039), Command `tb`.

### 2.4 Welle 4 — Einziger Writer-Pfad

Shared Ensure (ARCHITEKTUR §5.5):

- **US-004:** heal_logic, net, lock, mutate_service, `up` (inkl. A-031)
- **US-005:** `heal` one-shot + A-038-Tests (ruft ensure) — **nach** US-004
- **US-011:** Privilege-/argv-Regressionstests — nach Mutate

Formale `abhaengigkeiten` nur wellen-rückwärts; Startreihenfolge über Intra-Wellen-Liste in BACKLOG.

### 2.5 Welle 5 — Beobachten (Leuchttürme)

Ping/Health/status → topo + json → monitor → Partial-Cluster-Tests.  
Monitor und Topo brauchen keine echte HW (Fake-Ports/Fixtures).

### 2.6 Welle 6 — Ops und Abschluss

- Loop (US-016) vor LaunchAgent-Install (US-013), weil Plist `heal --loop` startet
- Service-Code in US-013; Uninstall/Status-AKs als Test-Stories US-014/015
- Doctor, bench, SSH, Kann (audit/rich), Install/Offline-Nachweise parallel

---

## 3. Prüfungen

### 3.1 Abdeckung A-001–A-045

Alle 45 Anforderungen sind in BACKLOG-Matrix Story-zugeordnet (Muss 31 · Soll 12 · Kann 2).

### 3.2 Dateibesitz disjunkt je Welle

Manuell geprüft: keine doppelten exakten Pfade innerhalb einer Welle.  
Verzeichnis-Suffix `/` impliziert exklusiven Unterbaum; Einzeldateien im selben Ordner (z. B. `tests/unit/health/test_*.py`) sind disjunkt, solange kein zweiter Agent den Ordner-Suffix besitzt.

### 3.3 Abhängigkeiten / Zyklen

- Alle `abhaengigkeiten` zeigen nur auf Stories **früherer** Wellen.
- Intra-Wellen-Kopplung (004→005, 006→007, 016→013, 018→019) über dokumentierte Reihenfolge, nicht über seitwärts-`abhaengigkeiten`.
- Graph azyklisch.

### 3.4 Jede Zieldatei ein Besitzer

Produktbaum aus ARCHITEKTUR §6 ist auf Stories verteilt (Gerüst US-000, Rest Fach-Stories). FAQ-Inhalte final in Abnahme; US-024 nur `docs/faq/.gitkeep`.

---

## 4. Hotspots

| Hotspot | Lösung |
|---|---|
| CLI-Registry | `parser.py` = US-000 |
| AppContext-Factory | US-000 + Adapter-Additivrecht |
| Shared Ensure up/heal | US-004 besitzt Logik; US-005 Command |
| heal --loop | US-016 Additivrecht auf heal.py |
| service * | ein Modul US-013 |
| topology fixtures | US-008 (partial_mesh inklusive) |

---

## 5. ANNAHMEN

| ID | Annahme |
|---|---|
| P-1 | 6 Wellen Vollausbau |
| P-2 | US-000 als Gerüst-Arbeitspaket |
| P-3 | Intra-Wellen-Reihenfolge für enge Kopplung |
| P-4 | A-031 (Lock) → US-004 |
| P-5 | FAQ-Inhalte in Abnahme-Phase |
| P-6 | partial_mesh-Fixture → US-008 |

---

## 6. Planungskonflikte

**Keine ungelösten Konflikte.**

---

## 7. Kennzahlen

| Metrik | Wert |
|---|---|
| Wellen | **6** |
| Stories gesamt | **27** |
| Stories je Welle | 1 / 4 / 2 / 3 / 5 / 12 |
| Muss-A abgedeckt | 31/31 |
| Soll-A abgedeckt | 12/12 |
| Kann-A abgedeckt | 2/2 |
