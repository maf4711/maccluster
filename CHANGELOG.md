# Changelog

## 1.3.1 — 2026-11-08

- rsync's stdout/stderr pipes are closed and rsync is reaped on every exit path (no more
  `ResourceWarning: unclosed file` from `_run_rsync_once`); the lock watcher keeps tracking it.
- Test only: the `docsync watch` Ctrl-C test waits for the hung listing child instead of counting
  descendants with `ps -A` (flaky under load).

## 1.3.0 — 2026-11-08

Rollout:
- Install 1.3.0 on the nodes only together with the maccluster release that passes `max_runtime`
  to `api.SyncRequest`; otherwise the peer lock caps at 1800 s + 60 s and maccluster syncs longer
  than 31 min abort with `peer lock lost (sleep or link down) — base not written`.
- Never write `llm-*` roots (or any root with `includes`) on a node with docsync < 1.3.0: older
  versions ignore `includes` and would sync all of `~/.claude`, `~/.codex`, `~/.gemini`,
  credentials included. Upgrade every node first.

- `docsync icloud-check [--root NAME] [--json]`: per node and root whether iCloud "Desktop &
  Documents" is on, how many files are not uploaded after 15 min and how many `brctl` uploads
  (fail) and downloads (reported) are stuck, plus cross-node parity (paths, size/mtime, iCloud ` 2`
  duplicates); exit 1 when not complete.
  Peers are probed with their own docsync (`icloud-check --local --json`); results are stored in
  `<state dir>/icloud-<node>.json`. An unreachable peer whose root passed the last check (<= 24 h)
  on both sides is reported as `unreachable — covered by iCloud` and no longer fails the sync.
- Per-root `includes` allowlist (root-anchored globs, `*`/`?` stop at `/`, `**` = any depth) in the
  local scan, the remote lister and `parse_lines`; non-matching directories are never entered.
  Such roots drop `DENY_PATTERNS` files (`.credentials.json`, `auth.json`, `*.pem`, `.env`, …) on
  both sides, `docsync plan` prints `denied: <n> file(s)`, the peer needs python3, and
  `install-agent` keeps them out of WatchPaths. New `docsync roots add-llm` appends the
  `llm-claude`, `llm-codex` and `llm-gemini` roots. Roots without `includes` are unchanged.
- Root lock on both sides of a peer pair: every `docsync sync` holds
  `<state dir>/locks/root-<sha1[:12]>.lock` for its root on this Mac and on the peer (taken there by a
  small Python 3.9 helper sent over ssh stdin) for the whole three-way run, so the same root of two
  Macs is never synced in both directions at once. The two locks are taken in a fixed order (by
  `<host>:<real root path>`), each with a wait of at most 1 s.
- A peer whose root is locked by another run is reported as `peer busy (covered by the other run)`:
  not a failure, no notification, base file untouched. The run exits 0 when the other peers are ok
  and 3 when every peer was busy. Root-lock records carry a 4th line, the host name; `run.lock` is unchanged.
- The peer-side lock ends within 5 s after its ssh session closes and within 30 s on a silent
  connection (heartbeat every 10 s, `DOCSYNC_LOCK_HEARTBEAT_S`; hard cap max-runtime + 60 s, or the
  request's `api.SyncRequest.max_runtime` + 60 s when no deadline is armed (`api.sync` from maccluster;
  default 1800 s, <= 0 = no hard cap, only the heartbeat bounds the helper).
- The peer acknowledges every heartbeat. Before each write phase (conflict copies, SafetyNet, push,
  pull, deletes) and before the base file is saved, a run checks that the last acknowledgement is
  younger than 2 heartbeats (wall clock, so a sleeping Mac counts); otherwise the peer fails with
  `peer lock lost (sleep or link down) — base not written`.
- When this Mac's own root lock is held by a run of another pair, the peer line reads
  `local root busy (pid N on <host>) — retried next run` instead of `peer busy`.
- When the run deadline fires, the stacks of all threads are written to
  `<log dir>/deadline-<UTC stamp>.txt` (`DOCSYNC_LOG_DIR`, default `~/Library/Logs/docsync`) and the
  history.log line names the file (`docsync.watchdog.dump_stacks`).
- Dry runs (`docsync plan`) take no root locks.
- A legacy base file (`base-<peer>.json`) is renamed only after both root locks are held, so a busy
  peer leaves every base file untouched; the peer-side record is cut at 4096 bytes on a character boundary.
