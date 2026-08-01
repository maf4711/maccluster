# Sicherheitsarchitektur-Review — MacCluster

| Feld | Wert |
|---|---|
| Projekt | MacCluster (`maccluster`) |
| Phase | 2 ARCHITEKTUR (Dokumenten-Review, kein Produktcode) |
| Stand | 2026-08-01 |
| Prüferrolle | Sicherheitsarchitekt / adversarial |
| Quellen | `ARCHITEKTUR.md`, `STACK.md`, ADRs, `ANFORDERUNGEN.md`, `NFA.md`, `RISIKEN.md`, `RANDFAELLE.md` |
| Qualitätsmaßstab | `company/QUALITAET.md` §4, `company/PROZESS.md` |

**Scope:** Architektur- und Anforderungsabgleich (AuthN/AuthZ, PII, Secrets, Angriffsfläche Subprocess/Config, Abhängigkeiten/Lizenzen). Kein Code-Change am Produkt; Fixes an Architektur-Doku bei kritisch/hoch.

---

## 1. Gesamturteil

| | |
|---|---|
| **Urteil** | **Nacharbeit nötig** (Architektur-Baseline war lückenhaft in §8.4; Hoch-Befunde in ARCHITEKTUR + ADR nachgezogen) |
| **Kritisch offen** | 0 |
| **Hoch (nach Fix in Doku)** | 0 offen / 3 behoben in Architektur |
| **Mittel offen (Implementierungsauflage)** | 4 |
| **Niedrig** | 3 |
| **Freigabe Planung/Implementierung** | **ja**, unter Einhaltung der erweiterten Security-Baseline in ARCHITEKTUR §8.4 und ADR-0002/0004 |
| **Freigabe Abnahme** | erst nach Code-Review Mutate-Pfad (Welle 4+) und SCA-Gate |

Produkt hat **noch keinen Implementierungscode** (nur `_fabrik/` + leeres Git). Secret-Scan und pip-audit beziehen sich auf Architektur-/Stack-Artefakte und geplante Deps.

---

## 2. Bedrohungsmodell (kurz)

| Asset | Bedrohung | Angreifer-Modell |
|---|---|---|
| Host-Netz (Wi-Fi/LAN/VPN) | Falsche Interface-Mutation | Fehlkonfig / bösartige `cluster.toml` / PATH-Hijack unter `sudo` |
| Cluster-Config / Lock / Plist | Symlink-Overwrite, TOCTOU | lokaler User mit Schreibrecht auf Config-Dir |
| Terminal / Operator | ANSI-Injection aus Hostnames | Config mit Escape-Sequenzen |
| Peer-Sicht (optional SSH) | Prompt-Hang, Host-Key-Bypass | MitM im Cluster-LAN (selten, Studio-Netz) |
| Lab-Topologie (HW-UUID, IPs) | Leak in Logs/Bug-Reports | Operator teilt Audit-Output |

**Nicht im Scope v1:** Multi-Tenant, öffentliche API, Remote-Auth, Cloud-Telemetrie (explizit out-of-scope). Auth = macOS-User + optional sudo (NFA-018).

---

## 3. Befunde (absteigend nach Schwere)

### S-01 — ProcessRunner ohne absolute Pfade / PATH-Hijack unter Privilege

| | |
|---|---|
| **Schwere** | **Hoch** |
| **Fundstelle** | `ARCHITEKTUR.md` §8.4 (vorher nur „Allowlist Basenames“); `adapters/process.py` geplant; ADR-0004 Punkt 3 |
| **Bezug** | A-044, NFA-022, R-R01, RF-X-06, RF-X-09 |
| **Szenario** | Operator führt `sudo maccluster up|heal` aus. ProcessRunner startet `ifconfig`/`networksetup` nur als Basename. Ein Eintrag früher im `PATH` (schreibbar für unprivilegierten User, z. B. manipulierter User-PATH der bei `sudo -E` oder unsicherem sudoers durchgereicht wird, oder LaunchAgent mit schlankem/unerwartetem PATH) liefert ein Fake-Binary → Code-Ausführung als root bzw. falsche Netzmutation. |
| **Gegenmaßnahme (verbindlich)** | ProcessRunner: (1) Basename-Allowlist; (2) Auflösung auf **absolute** Pfade nur aus festen Suchpfaden `/usr/sbin`, `/sbin`, `/usr/bin`, `/bin` (optional `/opt/homebrew/bin` nur für `iperf3`); (3) `shell=False` immer; (4) kein ungefiltertes Parent-ENV an Kinder (siehe S-04). LaunchAgent: absolute `ProgramArguments[0]`. |
| **Status** | **Behoben in Doku** — ARCHITEKTUR §8.4.2, ADR-0004 ergänzt |

### S-02 — Config-/Lock-Schreiben ohne Symlink-Policy

| | |
|---|---|
| **Schwere** | **Hoch** |
| **Fundstelle** | `ARCHITEKTUR.md` §5.4 / `adapters/filesystem.py` (0600, atomic, backup — **ohne** Symlink); RF-X-11 / RF-A21 |
| **Bezug** | NFA-017, A-004, A-027, NFA-027 |
| **Szenario** | `~/.config/maccluster/cluster.toml` oder `mutate.lock` ist Symlink auf sensible Datei (z. B. `~/.ssh/authorized_keys`, fremde Dotfile). `init --force` oder Lock-Create folgt dem Symlink und überschreibt/truncatet das Ziel (klassischer local symlink race). |
| **Gegenmaßnahme (verbindlich)** | Vor Create/Replace: Ziel darf **kein** Symlink sein (`lstat`, nicht `stat`); bei Exist + Symlink → Exit 2, keine Schreiboperation. Atomic write: temp im **selben Verzeichnis** + `os.replace`. Lock-Datei gleiche Policy. |
| **Status** | **Behoben in Doku** — ARCHITEKTUR §8.4.3, ADR-0002 ergänzt |

### S-03 — Interface-/Identifier-Validierung und NetworkApply-Verbote unvollständig spezifiziert

| | |
|---|---|
| **Schwere** | **Hoch** |
| **Fundstelle** | `ARCHITEKTUR.md` §5.3 „interface charset allowlist“ ohne Regex; §8.4 ohne Forbidden-Ops-Liste; ADR-0004 teilweise |
| **Bezug** | A-009, A-041, A-044, RF-F3-15, R-T04, R-D02, R-R01 |
| **Szenario** | Config enthält `bridge_interface = "en0;rm -rf /"` oder steuert Wi-Fi/Default-Route. Ohne harte Identifier-Regex greift Defense nur, wenn Implementierer argv-only korrekt umsetzt; zusätzlich können erlaubte Tools mit **falschen Flags/Targets** (DNS global, Power off Wi-Fi) Schaden anrichten, selbst ohne Shell. |
| **Gegenmaßnahme (verbindlich)** | (1) Interface-Namen: `^[A-Za-z][A-Za-z0-9_.-]{0,15}$` (macOS-üblich, fail-closed). (2) Node-id/hostname: restriktive Zeichensätze; Hostnames für Anzeige sanitizen. (3) NetworkApply **nur** Ensure-Bridge / admin-up / Self-IP auf allowlisted iface; **verboten:** Default-Route, globale DNS, Wi-Fi power, fremde en0-IPs, remote SSH-Write. (4) Unit-Tests mit Injection-Strings (RF-F3-15). |
| **Status** | **Behoben in Doku** — ARCHITEKTUR §8.4.4–8.4.5 |

### S-04 — Subprocess-Environment nicht gehärtet

| | |
|---|---|
| **Schwere** | **Mittel** |
| **Fundstelle** | ProcessRunner-Vertrag in Entwürfen; fehlte in verbindlicher ARCH §8.4; RF-X-09 |
| **Szenario** | Unter Privilege werden `DYLD_*`, manipulierter `PATH`, `IFS` o. ä. an OS-Tools durchgereicht und ändern Lade-/Parseverhalten. |
| **Gegenmaßnahme** | Minimal-ENV an Kinder: `PATH=/usr/bin:/bin:/usr/sbin:/sbin`, `HOME`, `USER`, `LANG`/`LC_ALL=C` wo sinnvoll; **keine** Weitergabe von `DYLD_*`. Documented in ARCH §8.4.2. |
| **Status** | **Auflage Implementierung** (in ARCH spezifiziert) |

### S-05 — Terminal-/ANSI-Injection in Ausgaben

| | |
|---|---|
| **Schwere** | **Mittel** |
| **Fundstelle** | `render/sanitize.py` im Baum genannt; Security-Abschnitt ohne Pflicht; RF-F5-20 |
| **Szenario** | Hostname/id in Config enthält CSI-Sequenzen → Monitor/status verfälscht Terminal (Title, clear, spoofed „UP“). |
| **Gegenmaßnahme** | Alle Config-/OS-Strings vor Terminal-Ausgabe durch `render/sanitize` (Control-Chars strip/escape); JSON-Modus unverändert strukturell, aber ohne Roh-ANSI-Pflicht. Tests RF-F5-20. |
| **Status** | **Auflage Implementierung** (ARCH §8.4.6) |

### S-06 — SSH-Probe-Härtung unvollständig in Security-Sektion

| | |
|---|---|
| **Schwere** | **Mittel** |
| **Fundstelle** | D-10 BatchMode+Timeout; RF-F5-07/08, NFA-023, A-032 |
| **Szenario** | SSH ohne BatchMode hängt an Password-Prompt; Auto-Accept Host-Keys öffnet MitM; Key-Material in Verbose-Logs. |
| **Gegenmaßnahme** | `ssh -o BatchMode=yes -o ConnectTimeout=3 -o PasswordAuthentication=no -o KbdInteractiveAuthentication=no`; Host-Key-Fehler → warn + local fallback, **kein** `StrictHostKeyChecking=no` Default; nie Key-Inhalte loggen. |
| **Status** | **Auflage Implementierung** (ARCH §8.4.7) |

### S-07 — LaunchAgent / Audit: Rest-Risiken dokumentiert, nicht blockierend

| | |
|---|---|
| **Schwere** | **Mittel** (Privilege best-effort) / **Niedrig** (Audit-Leak) |
| **Fundstelle** | ADR-0005; R1 ARCH; R-R02; A-036 |
| **Szenario** | User-Agent ohne Root: silent success wäre kritisch — Architektur verbietet das bereits. Audit-Log enthält HW-UUID/IPs wenn aktiviert. |
| **Gegenmaßnahme** | Privilege honesty bleibt; Audit Default aus; Log nur Aktion+Ergebnis+iface/IP, keine Secrets; README „logs may contain host identifiers“. |
| **Status** | **Akzeptiert mit Doku** (kein Architektur-Blocker) |

### S-08 — Config-Größenlimit / Bench-Target

| | |
|---|---|
| **Schwere** | **Niedrig** |
| **Fundstelle** | RF-F2-18, RF-F7-11; ARCH Validierung ohne Size-Cap |
| **Szenario** | Multi-MB TOML oder Bench-Arg mit Spaces/`$(…)` — DoS/Injection wenn Validierung fehlt. |
| **Gegenmaßnahme** | Config max 1 MiB; Bench-Ziel nur validierte IP aus Config oder strikte IP-Parse; kein freier Shell-String. |
| **Status** | **Auflage Implementierung** (ARCH §8.4.8) |

### S-09 — Kein App-Crypto / Klartext-Config (NFA-026)

| | |
|---|---|
| **Schwere** | **Akzeptiert (by design)** |
| **Fundstelle** | NFA-026, NFA-027 |
| **Szenario** | Lokaler Reader mit Dateizugriff liest Cluster-Plan. |
| **Gegenmaßnahme** | `0600` bei Neuanlage; kein Secret-Inhalt in Config; Operator-Backup/Dotfiles. Kein zusätzliches App-Crypto in v1. |
| **Status** | **Akzeptierbar** |

---

## 4. Themen-Checkliste

### 4.1 Authentifizierung (AuthN)

| Anforderung | Architektur | Bewertung |
|---|---|---|
| NFA-018 kein App-Login | OS-User only; keine Login-Commands | **OK** |
| A-028/A-012 Privilege melden | Exit 1 `admin/sudo required`; kein silent success | **OK** |
| Keine Credential-Speicherung | SSH Keys nur OS-Agent | **OK** |

### 4.2 Autorisierung (AuthZ)

| Anforderung | Architektur | Bewertung |
|---|---|---|
| Eine Rolle Operator | Brief/ANF | **OK** |
| Least Privilege RO (NFA-019, A-029) | Read vs mutate split; NetworkRead vs Apply | **OK** |
| A-041 nur lokal mutieren | ADR-0004; kein SSH-Write | **OK** |
| A-043 Platform guard | mutate Exit 2 unsupported | **OK** |

### 4.3 PII / Datenschutz

| Anforderung | Architektur | Bewertung |
|---|---|---|
| NFA-028 keine Personen-PII | Tech-Felder Host/IP/HW-UUID | **OK** |
| NFA-029 keine Telemetrie | kein Server, offline | **OK** |
| NFA-030 lokale Datenhoheit | dokumentierte Pfade | **OK** |
| R-R02 Geräte-IDs in Logs | Audit opt-in; README-Hinweis nötig | **OK mit Auflage S-07** |

### 4.4 Secrets

| Anforderung | Architektur | Bewertung |
|---|---|---|
| NFA-020 / A-044 keine Secrets im Repo | §8.4; examples placeholders | **OK (Architektur)** |
| Config ohne Tokens | cluster.toml nur Netzplan | **OK** |
| Key-Material nie in stdout/JSON | A-044 Abnahme | **Auflage Code-Review** |

### 4.5 Angriffsfläche Subprocess / Config

| Kontrolle | Vor Review | Nach Fix |
|---|---|---|
| `shell=False`, ein Tor `adapters/process.py` | ja | ja |
| Basename-Allowlist | erwähnt | **explizite Liste + absolute Pfade** |
| Interface-Charset | vage | **Regex + Forbidden-Ops** |
| Symlink-Policy | fehlte | **verbindlich** |
| Timeouts A-045 | ja | ja |
| ENV-Härtung | fehlte | **Minimal-ENV** |
| Output-Sanitize | Modul geplant, keine Pflicht | **Pflicht** |
| SSH BatchMode | D-10 | **Flags vollständig** |

### 4.6 Abhängigkeiten / Lizenzen

| Komponente | Lizenz | Runtime | Risiko |
|---|---|---|---|
| Python stdlib | PSF | ja | niedrig |
| rich (optional) | MIT | optional | niedrig; pin `<15` |
| hatchling / pytest / ruff | MIT | build/dev | niedrig |
| pip-audit | Apache-2.0 | CI | ok |
| Produkt | MIT | — | ok |
| GPL/AGPL Runtime | — | **keine** | ok |

**SCA:** Kein Produkt-`pyproject`/Lockfile im Repo-Stand. Geplant: `install_requires: []`, Dependabot, `pip-audit` in verify/CI (STACK §4, NFA-025).  
**Audit-Ergebnis (Architektur-Phase):** Werkzeug `pip-audit` — **nicht lauffähig** (kein Package-Tree). Geplante Runtime-CVE-Fläche ≈ **0** (stdlib); optional rich → bei Aufnahme pin + Audit. **Lizenzcheck Stack-Tabelle: konform.**

---

## 5. Secret-Suche

| Scope | Methode | Ergebnis |
|---|---|---|
| `projects/maccluster/` Produktcode | n/a — **kein Source-Tree** | — |
| `_fabrik/**` + ADRs + STACK | manuell / Muster (password, token, private key, BEGIN) | **keine eingebetteten Secrets** |
| Beispiel-Config (geplant) | placeholders in ARCH §5.3 | **OK** (keine echten UUIDs/Keys) |
| Git-Historie Produkt | leeres/init Repo | **kein Secret-Fund** |

**Regel:** Gefundene Secrets würden nur als Fundstelle+Typ gemeldet — hier: null.

---

## 6. Geprüfte Bereiche ohne Befund

- Kein HTTP-Server / keine öffentliche API (NFA-011) — Angriffsfläche netzseitig minimal.
- Kein Multi-User-App-RBAC nötig (eine Operator-Rolle).
- Offline-first / keine Telemetrie-Endpoints.
- Dual TB-Probe + fail-closed Mapping (A-039) reduziert Falsch-Mutation.
- Single-writer Lock-Konzept (A-031) vorhanden (Symlink-Lücke war separat S-02).
- Exit-Code-Semantik AD-3 skriptierbar (kein false Exit 0 bei Privilege-Fail laut Design).
- Optional rich lazy — Kern ohne Extra-Dep.
- Keine Root-Helper/setuid in v1 (kleinere dauerhafte Privilege-Fläche; Trade-off R1 akzeptiert).

---

## 7. Traceability NFA Security → Architektur (nach Fix)

| NFA/A | Mechanismus |
|---|---|
| NFA-018 | kein Login; OS-Auth |
| NFA-019 | RO ohne Root; mutate meldet Admin |
| NFA-020 | keine Secrets in Repo/Config |
| NFA-021 | validate pure + Exit 2 |
| NFA-022 | ProcessRunner argv + abs path + allowlist |
| NFA-023 | SSH optional, BatchMode, Timeout |
| NFA-024 | Audit opt-in, keine Secrets |
| NFA-025 | stdlib + rich MIT; pip-audit; Dependabot |
| NFA-026 | Klartext + 0600; kein App-Crypto |
| NFA-027 | Config 0600 |
| NFA-028–031 | Tech-Daten, local-only, README |
| A-041 | NetworkApply local only |
| A-044 | Secrets + argv + Tests Sonderzeichen |

---

## 8. Auflagen für Implementierung / QA

1. Unit-Tests: ProcessRunner lehnt non-allowlist und relative-only argv ab; Injection-Fixtures RF-F3-15, RF-F5-20, RF-F7-11.
2. Filesystem-Tests: Symlink an Config- und Lock-Pfad → Exit 2, Ziel unverändert.
3. SSH-Adapter-Tests: BatchMode in argv; kein Password-Prompt-Pfad.
4. Welle-4 Security-Review auf `network_apply` + `process` (ARCH §14).
5. Abnahme: `pip-audit` clean critical/high; Dependabot vorhanden; Secret-Scan CI optional.

---

## 9. Änderungshistorie dieses Reviews

| Datum | Aktion |
|---|---|
| 2026-08-01 | Erst-Review Architektur; S-01–S-03 Hoch → Fixes in `ARCHITEKTUR.md` §8.4, ADR-0002, ADR-0004 |

---

*Ende SICHERHEIT.md*
