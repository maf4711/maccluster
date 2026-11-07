# docsync

Terminal tool that keeps `~/Documents` identical on several Macs over the LAN.
Bidirectional, rsync+ssh, no cloud, no daemon required. Python 3.11+ stdlib only.

```
docsync init --from-maccluster      # peers from ~/.config/maccluster/cluster.toml
docsync doctor                      # rsync, ssh keys, peer reachability
docsync plan                        # dry run: what would move
docsync sync                        # do it, then print the report
docsync install-agent --interval 15 # launchd: every 15 min in the background
```

## How a sync works

For every peer docsync lists both trees (`mtime`, `size` per file) and compares
them with the **base** — the state after the last successful sync with that peer
(`~/.local/state/docsync/base-<peer>.json`). That makes it a real 3-way merge:

| local vs base | remote vs base | action |
|---|---|---|
| changed | unchanged | push → |
| unchanged | changed | ← pull |
| deleted | unchanged | delete on peer |
| unchanged | deleted | delete here |
| changed | changed | **conflict** |
| deleted | changed | pull (edit wins over delete) |

Conflicts never lose data: the loser is renamed to
`name.conflict-<timestamp>-<host>.ext` and both files end up on both Macs.
`conflict_policy` in the config chooses the winner: `newest` (default), `local`, `remote`.
`docsync conflicts` lists them. Deletions go to `~/.Trash/docsync-<timestamp>/`
on the affected machine (`trash = false` to unlink instead).

The first sync with a peer has no base: both trees are merged (union, newest
wins), nothing is deleted.

## Report

Every `sync`/`plan` ends with a report per peer and in total: files and bytes
pushed/pulled, deletions, conflicts, unchanged files, bytes on the wire,
time per phase (connect/list/push/pull/delete) and throughput. Reports are
appended to `~/.local/state/docsync/history.log` (`docsync history`).

## Config — `~/.config/docsync/config.toml`

```toml
path = "~/Documents"
conflict_policy = "newest"
trash = true
excludes = [".DS_Store", "*.icloud", "~$*", ...]

[[peers]]
name = "mini"
hosts = ["10.42.0.1", "CM-CFMQ2D029F.local"]   # tried in order (Thunderbolt first)
user = "a321"
path = "~/Documents"
```

Each peer needs Remote Login enabled and your ssh key in its `authorized_keys`
(`ssh-copy-id user@host`). `docsync discover` lists Macs advertising ssh via Bonjour.

## Install

```
make test
make install        # symlink ~/.local/bin/docsync → this checkout
```

## Files

- `docsync/engine.py` — the 3-way planner (pure, fully unit-tested)
- `docsync/manifest.py` — tree scan, excludes, remote lister
- `docsync/transport.py` — rsync/ssh and local-directory transports
- `docsync/session.py` — one run against one peer
- `docsync/report.py` — the final report
- `docsync/cli.py` — commands
