---
name: maccluster-status
description: >
  Zeigt MacCluster-Historie (Releases, Git, Config, Service, Audit) und aktuelle
  Monitor-Daten (status, traffic TX/RX, doctor, TB, topo). Trigger: /maccluster-status,
  /maccluster, "maccluster status", "maccluster monitor", "maccluster historie",
  "cluster status", "TB cluster", "thunderbolt cluster stand", "zeig maccluster".
---

# MacCluster Status + Historie

Du bist der Operator-Assistent für **MacCluster** (Thunderbolt-Mesh CLI auf Apple Silicon Mac minis).

## Wann

User will den **aktuellen Cluster-Stand** und/oder die **Historie/Entwicklung** von MacCluster sehen.

## Ablauf (immer in dieser Reihenfolge)

### 1) Report-Skript ausführen

```bash
export PATH="$HOME/.local/bin:/opt/homebrew/bin:$PATH"
bash "$HOME/.grok/skills/maccluster-status/scripts/maccluster-report.sh"
```

Falls das Skript fehlt oder scheitert: manuell die Kommandos aus Abschnitt „Fallback“ laufen.

### 2) Optional: Live-Monitor-Stichprobe (2 Samples für Rates)

Nur wenn User „live“, „monitor“, „rates“ oder „traffic“ sagt:

```bash
export PATH="$HOME/.local/bin:$PATH"
maccluster --json status 2>/dev/null || true
sleep 1.2
maccluster status
```

### 3) Ausgabe an den User (kompakt, Deutsch)

Strukturiere die Antwort so:

#### A) Jetzt — Live-Monitor

Tabelle oder Klartext:

| Feld | Wert |
|------|------|
| overall | healthy / degraded / … |
| bridge | interface + IPs |
| nodes | id, IP, UP/DOWN, via=ping\|tcp:22, rtt |
| traffic | iface RX/TX (Mb/s), pps, errors |
| TB | ports connected / speeds |
| service | LaunchAgent installed/running |

Nenne explizit, wenn Rates `n/a` sind (zweites Sample nötig).

#### B) Historie

1. **Produkt-Version** (pipx / CLI)
2. **Git-Historie** (letzte 8–12 Commits aus dem Repo, falls vorhanden)
3. **CHANGELOG** (Top-Einträge)
4. **GitHub Releases** (optional `gh release list -R maf4711/maccluster -L 5`)
5. **Lokaler Betrieb**
   - Config-Pfad + mtime + Node-Anzahl
   - LaunchAgent-Plist
   - Audit-Log (letzte Zeilen, falls vorhanden)
   - Traffic-Cache mtime

#### C) Nächster Schritt (1–2 Zeilen)

z. B. Peer-Install, `sudo maccluster up`, `ssh-copy-id`, Kabel stecken — nur wenn Doctor/Status das nahelegt.

## Pfade (Source of Truth)

| Was | Pfad |
|-----|------|
| CLI | `$HOME/.local/bin/maccluster` (pipx) |
| Config | `~/.config/maccluster/cluster.toml` |
| Traffic-Cache | `~/Library/Caches/maccluster/traffic_sample.json` |
| Audit (optional) | `~/.local/state/maccluster/actions.log` |
| LaunchAgent | `~/Library/LaunchAgents/com.maccluster.heal.plist` |
| Repo (lokal) | `~/Developer/fabrik/projects/maccluster` oder Clone von GitHub |
| GitHub | https://github.com/maf4711/maccluster |

Env: `MACCLUSTER_CONFIG` überschreibt Config-Pfad.  
`PATH` muss `~/.local/bin` enthalten.

## Fallback (ohne Skript)

```bash
export PATH="$HOME/.local/bin:$PATH"
command -v maccluster && maccluster --version
maccluster doctor
maccluster status
maccluster tb 2>/dev/null | head -40
maccluster service status 2>/dev/null
maccluster topo 2>/dev/null | head -30
test -f ~/.config/maccluster/cluster.toml && wc -l ~/.config/maccluster/cluster.toml
test -f ~/.local/state/maccluster/actions.log && tail -20 ~/.local/state/maccluster/actions.log
REPO="$HOME/Developer/fabrik/projects/maccluster"
if [ -d "$REPO/.git" ]; then git -C "$REPO" log --oneline -12; fi
if [ -f "$REPO/CHANGELOG.md" ]; then head -40 "$REPO/CHANGELOG.md"; fi
gh release list -R maf4711/maccluster -L 5 2>/dev/null || true
```

## Regeln

- **Read-only** bzgl. Cluster: keine `up`/`heal`/`service install` ohne expliziten User-Wunsch.
- Keine langen Exkurse — Daten zuerst, dann 2–3 Sätze Interpretation.
- Sprache des Users matchen (meist Deutsch).
- Wenn CLI fehlt: Install-Hinweis aus `docs/INSTALL.md` / one-liner raw.githubusercontent.com.
- Nicht mit exo/merados-swarm vermischen — reines MacCluster.

## Slash

`/maccluster-status` · Alias-Trigger: `/maccluster`
