# maccluster — Arbeitsregeln

## Multi-Mac: dieses Repo wird von mehreren Cluster-Macs parallel bearbeitet

- **Vor jedem Merge, Rebase oder Integrations-Schritt:** `git fetch --all`,
  dann `git log main..origin/main` und `git branch -r` prüfen. Ein
  vorgelaufenes `origin/main` ist die Regel (29.08.2026: `c9f651d` "per-peer
  RDMA status in doctor" kam von einem anderen Mac, während hier auf einem
  Branch an denselben Dateien gearbeitet wurde).
- **Nie einen fremden Branch oder Commit mit einem lokalen Snapshot
  überschreiben.** Erst ansehen (`git show --stat <sha>`), dann mergen und
  beide Absichten erhalten; bei Konflikten mit `pytest` + `ruff` belegen.
- Kein `push --force` auf geteilte Branches. Worktrees pro Workstream
  (`../maccluster-wt/<name>`), nie zwei Schreiber im selben Tree.
- `~/.config/maccluster/cluster.toml` und `rdma_ctl` werden nie von Agenten
  verändert; `sync … --apply` nur auf ausdrückliche Anweisung.

## Build & Test

```bash
.venv/bin/python -m pytest -q
.venv/bin/ruff check src tests && .venv/bin/ruff format --check src tests
```

`src/maccluster/services/sync_service.py` ist bereits >2600 Zeilen — neue
Logik gehört in eigene Module, die Datei darf nicht weiter wachsen.
