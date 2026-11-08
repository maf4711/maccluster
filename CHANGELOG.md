# Changelog

## 1.3.3 — unreleased

- `docsync monitor [--once] [--json] [--interval SEC] [--local]` (incident 2026-11-08: with the
  agents paused on all three Macs, syncs kept running from an unknown caller and nothing showed
  it). One row per node (this Mac and every peer): launchd agent (loaded, state, last exit,
  `<python> -m docsync` or foreign program, interval, `--max-runtime`), the run/root lock holders
  that are alive, the last run and the age of the last success from `history.log`, open conflict
  copies, deadline dumps of the last 24 h and the last iCloud check. FAIL/WARN alerts, exit 1 on
  any FAIL. Peers answer through `monitor --local --json` over ssh; a peer without the command
  shows `docsync too old on <peer> (needs 1.3.3)`. Read-only. JSON schema in the README.
- Every monitor alert has a stable `code` and `data` (15 codes, table in the README), computed by
  the pure `docsync.monitor_alerts.alerts()` that `heal` will reuse.
- The agent is the node's own when it runs `<python> -m docsync` with an existing interpreter whose
  docsync is at least the monitor's version; otherwise `agent_foreign` with `reason` `shim`,
  `missing interpreter` or `old <v>` (below `AGENT_MIN_VERSION` 1.3.2).
- No false FAIL from a peer: exit 2 with failed peers is `peer_failed` (WARN), and staleness counts
  the last run where this node synced with at least one peer. A fresh install is `never_synced`
  (WARN) for 2 × interval + 30 min. Success is read from the parsed TOTAL counts only (ok ≥ 1,
  failed = 0, busy allowed). The monitor is bounded to about 45 s (probe 45 s, ssh ConnectTimeout 5 s,
  ping 2 s, conflict scan 20 s).
- `sync`/`plan --from-heal`: the origin line reads `agent=heal`; such runs count as runs and raise no
  `non_agent_run`. The agent's exit 2 is judged by its own last run (newest `agent=yes` block), so a
  later clean heal or manual sync does not turn a peer failure into `agent_exit`.
- Every `sync`/`plan` writes `origin: <argv> | parent <ppid> <parent command> | agent=<yes|no>`
  into its `history.log` block, and after the `deadline … exceeded` event.
- The ssh probe of `icloud-check` (PROBE_SH and its helpers) moved to `docsync/probe.py`, shared
  with `monitor`; `docsync conflicts` and `monitor` share one conflict scan.
||||||| 42c2ed3
- Peer lock: default heartbeat 30 s (was 10 s) and ssh `ServerAliveInterval=30`. Under heavy load
  (load 15–20, clamscan; process starts stalled up to 27 s on 2026-11-08) long syncs were aborted
  after 3–4 min with `peer lock lost` although both Macs were up. Now the client gives up after
  60 s without an ack and the peer helper frees the lock after 90 s of silence (the client still
  stops first); an ended ssh session still frees the lock at once (EOF).

## 1.3.2 — 2026-11-08

- Symlink guard (incident 2026-11-08: the first `llm-claude` sync on node-c replaced the symlink
  `~/.claude/projects/-Users-a321-Developer/memory` → dotfiles with a real directory). docsync no
  longer writes through, replaces or deletes a symlink on either side, and never writes into a
  path with a symlinked component on the receiving side. Such paths (the symlink itself and
  everything below it, on both sides) are left out of the run, reported as
  `skipped (symlink on <node>): <n> path(s)` and never count as a deletion. Everything else
  syncs normally.
- The local scan and both peer listers (LISTER_PY, still Python 3.9, and the `find`/`stat`
  fallback) report symlinks (to dirs, to files, dangling) as name-only `-3` records. Listings of
  trees without symlinks are byte-identical to 1.3.1.
- No rsync flag changes: `--keep-dirlinks` would write through a receiver's dir symlink into its
  target, and the plan never contains a path below a symlink, so rsync never meets one.
- Base entries below a skipped symlink are dropped (cloud-only files keep theirs): removing the
  link later pulls the files back instead of deleting them on the peer.
- A transport whose `manifest()` takes neither `links` nor `**kw` gets the warning
  `peer symlink protection unavailable for <transport>` instead of silently losing the guard.
- Known limit: a root that is itself a symlink (e.g. `~/.claude -> elsewhere`) is still followed.

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
