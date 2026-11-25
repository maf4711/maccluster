# docsync

> Since maccluster 0.9.0, docsync ships **inside maccluster** (this directory, merged with
> history from maf4711/docsync). Install maccluster to get both `maccluster` and `docsync`;
> `maccluster docsync …` is the same CLI. There is no separate docsync-lan wheel any more.

Terminal tool that keeps `~/Documents` identical on several Macs over the LAN.
Bidirectional, rsync+ssh, no cloud, no daemon required. Python 3.11+ stdlib only.

```
docsync init --from-maccluster      # peers from ~/.config/maccluster/cluster.toml
docsync doctor                      # rsync, ssh keys, peer reachability
docsync plan                        # dry run: what would move
docsync sync                        # do it, then print the report
docsync tb                          # Thunderbolt ports, link, speed, cabling sketch
docsync speedtest --all-hosts       # raw TCP + ssh throughput per peer and host (TB vs LAN)
docsync advise                      # on-device Apple Foundation Model: what to do about conflicts etc.
docsync install-agent --interval 15 # launchd: every 15 min in the background
docsync monitor                     # agent, running sync, last run, alerts on every node
```

## Roots

By default docsync syncs `~/Documents` (the top-level `path`, shown as root `documents`).
More folders are added as roots; each keeps its own base per peer
(`~/.local/state/docsync/base-<root>-<peer>.json`):

```toml
path = "~/Documents"          # root "documents" (optional once [[roots]] exist)

[[roots]]
name = "desktop"
path = "~/Desktop"

[[roots]]
name = "fonts"
path = "~/Library/Fonts"
peer_path = "~/Library/Fonts" # default: same as path
excludes = [".DS_Store"]      # default: the global excludes
```

`docsync sync --root desktop` limits a run to some roots. Roots run one after another, peers
in parallel within each root. A root whose folder does not exist fails with `root missing`
and is never created.

### Allowlist roots (`includes`) and `docsync roots add-llm`

A root with `includes` syncs only the files whose path relative to the root matches one of the
patterns: anchored at the root, `*` and `?` never cross `/`, `**` matches any depth
(`CLAUDE.md` does not match `sub/CLAUDE.md`). Directories no pattern can reach are never
entered, on this Mac and on the peer (a 4 GB `~/.codex` is not walked, only its allowlisted parts). Files outside
the allowlist are neither copied nor deleted on either side; symlinks (and symlinked folders)
are skipped as everywhere. Conflict copies (`CLAUDE.conflict-<stamp>-<host>.md`) in such a root are
tracked like their original file, so deleting the loser propagates.

```toml
[[roots]]
name = "llm-claude"
path = "~/.claude"
includes = ["CLAUDE.md", "settings.json", "MEMORY.md", "RTK.md", "projects/*/memory/**"]
```

In such a root the deny list (`.credentials.json`, `auth.json`, `oauth_creds.json`, `*.pem`,
`id_rsa*`, `id_ed25519*`, `.env`, any path component, ignoring case) wins over every include:
those files are never listed or sent, and `docsync plan` prints `denied: <n> file(s)` per root.
Roots without `includes` have no deny list (a Keynote `Talk.key` in Documents syncs as before).
An includes root needs python3 on the peer (`includes need python3 on the peer`; no `find`
fallback), and `docsync install-agent` leaves it out of WatchPaths: it syncs on the interval.

`docsync roots add-llm` appends the roots `llm-claude` (`~/.claude`), `llm-codex` (`~/.codex`:
`config.toml`, `AGENTS.md`, `rules/**`) and `llm-gemini` (`~/.gemini`: `settings.json`,
`GEMINI.md`) to `config.toml` as plain text, keeping every existing byte; run twice it prints
`llm roots already present`.

## Fidelity

rsync runs with `-X -A --crtimes` whenever both Macs have an rsync that supports them
(Homebrew rsync 3.x; macOS' `/usr/bin/rsync` is openrsync and does not). docsync probes the
peer once per run, uses its best rsync via `--rsync-path`, and warns in the report otherwise.
Kernel-protected attributes (`com.apple.provenance`, `com.apple.macl`, `com.apple.rootless`)
are skipped. Not preserved: BSD file flags (`chflags`), and files whose own ACL denies
`delete` (rsync cannot rename its temp file onto them; such files are reported as skipped).

## SafetyNet

Before a pull or push overwrites an existing file, that file is cloned into
`<root>/.docsync-safetynet/<UTC stamp>/<path>` on the side being overwritten (APFS clone:
instant, no extra space until a side changes). If a backup cannot be written, the peer's
transfer is aborted before anything is overwritten. Retention runs at the start of every real
sync on both sides: at most `safetynet_keep` (5) runs, none older than
`safetynet_max_age_days` (14), at most `safetynet_max_gb` (20) per root; the newest run is
always kept. `docsync safetynet` shows the usage, `docsync safetynet prune` applies the policy
now, `safetynet = false` turns backups off.

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

**Case-insensitive paths.** APFS treats `Gesundheit/Befunde` and `Gesundheit/befunde` as the
same folder, so docsync compares paths ignoring case (`case_insensitive = true`) while using
each side's own spelling for rsync. Without this, a case-only difference between two Macs
looks like "deleted on the peer" and would wipe the folder locally.

**Deletion guard.** More than `max_delete` deletions in one run (default 25) are held back
and reported; review with `docsync plan -v` and rerun with `docsync sync --force-deletes`
if they are intended. A peer mid-reboot, a renamed folder or an unmounted volume must never
turn into a mass delete.

## Advisor (Apple Foundation Models, on device)

`docsync advise [peer]` hands the last report from `history.log`, every conflict copy with
its original (size, modification time), the warnings and the iCloud counts to the on-device
Apple Foundation Model and prints up to eight recommendations in German: which version to keep
(by size and time, never by guessing content), whether held-back deletions look like a rename or
case change, and which peer to sync first. The model runs locally through a small Swift helper
(`bin/docsync-advisor.swift`, compiled once with `swiftc` into `~/.local/state/docsync/bin/`);
nothing leaves the Mac. `docsync advise --check` prints the model availability. When the model
is unavailable (device not eligible, Apple Intelligence off, model downloading, no swiftc) the
same inputs go through a deterministic rule set instead and the command exits 1 with the reason.
The advisor never executes anything; it only suggests docsync commands and file decisions.

## Performance

- **Bulk ssh is separate from control ssh.** rsync runs over its own connections with
  `aes128-gcm`, no compression and no ControlMaster multiplexing. Measured on Thunderbolt
  80 Gb/s: the default ssh (mux + compression + chacha20) uploads at 40 MB/s, one plain
  aes128-gcm connection at 1.7 GB/s, four of them at 4 GB/s. Listing, trash and rename still use
  the normal ssh (and your ControlMaster, which saves the handshake).
- **Parallel streams.** Transfers above 16 files / 64 MB are split size-balanced into
  `transfer_streams` (default 4) rsync processes; the progress bar aggregates them and the report
  shows `streams N`.
- **One local walk per run.** `sync` scans the local tree once, shared by all peers, while the
  remote listings run concurrently; after the transfer only touched paths are re-stated instead
  of walking the tree again. Exclude matching uses one compiled regex.
- Typical no-change run with two peers over Thunderbolt: 3.5 s (was 14.5 s).

## Unattended runs

- Transport failures (ssh banner timeout, peer rebooting, link flap, rsync timeout) are retried
  up to three times with 5/15/45 s delays; the report shows `retries N`.
- `docsync sync --wait SEC` waits for a running sync instead of exiting with code 3. The
  exit-3 message names the run that holds the lock: `another run is in progress (pid N,
  running M min, command)`; `~/.local/state/docsync/run.lock` holds the same three lines.
- Every `docsync sync` / `plan` has a wall-clock deadline, `--max-runtime SEC` (default 1800,
  `0` = no limit). When it passes, docsync writes `deadline of N s exceeded` to `history.log`
  and stderr, kills its own rsync/ssh child processes and exits with code 4, which releases
  the lock. No base is written for an unfinished peer, so the next run resumes the work.
  `docsync watch` applies the deadline to each run; the loop keeps going. The launchd agent
  passes `--max-runtime 1800`.
- `docsync watch` backs off exponentially after a failed run (interval → 2× … 6×) and keeps
  running on unexpected errors, which it writes to `history.log`.
- After a run that failed, produced a conflict copy or held deletions back, a Notification
  Center message appears (`--no-notify` to silence).
- `docsync install-agent` adds a launchd `WatchPaths` trigger on the Documents folder next to the
  interval (`--no-watch` for interval only). With `skip_on_battery = true` in the config the
  agent's runs do nothing on battery power.

## Monitor

`docsync monitor` shows this Mac and every configured peer in one table, then the alerts.
It only reads: it never syncs, never takes a lock and never writes state.

```
node    agent                          running now   last run                                        last ok  confl  dumps  icloud
node-c  running exit 0 /15m max 1800s  idle          11-08 14:54 2/0/0 ok/fail/busy 3 files [agent]  4m       0      0      ok 49m
node-a  not loaded                     idle          11-08 13:10 2/0/0 ok/fail/busy 0 files [agent]  1h 48m   0      0      -
node-b  docsync too old on node-b (needs 1.3.3)
FAIL node-a: agent not loaded
WARN node-b: docsync too old on node-b (needs 1.3.3)
monitor: FAIL
```

- **agent**: from `launchctl print gui/<uid>/com.a321.docsync`: state, last exit, interval, the
  `--max-runtime` value, and `FOREIGN` unless the program is `<python> -m docsync` with a `<python>`
  that exists and whose docsync (`<python> -m docsync --version`, run from `/` without `PYTHONPATH`,
  10 s, once per report) is at least `AGENT_MIN_VERSION` (1.3.2, the symlink-guard floor; bumped on
  purpose, not with every release). Any other form (a `docsync-launchd.py` shim, …) is `shim`; the
  reason is `shim`, `missing interpreter` or `old <v>` (`old ?` when it does not answer). Which
  Python runs the monitor does not matter. The plist's mtime gives how long the agent is loaded.
- **running now**: the `run.lock` holder and the root locks in `<state>/locks/` whose pid answers
  `kill -0`. A root-lock record written for another Mac's run cannot be checked here (`alive: null`).
- **last run**: the newest report block in `history.log` (DRY RUN and all-busy runs are left out)
  or `deadline of N s exceeded` event: peers ok/failed/busy, the failed peers with their reason,
  files, conflicts, `peer lock lost`, `skipped (symlink …)` and held-back deletions, and its origin.
  The rule for a run reads only the parsed `peers N ok, M failed[, K busy]` line, never the rest of
  the block (the origin line is free text): **success** = ok ≥ 1 and failed = 0, busy allowed
  (`last_success`); **synced** = ok ≥ 1 whatever failed (`last_synced`). **last ok** shows the age
  of `last_synced`: a node that syncs with its reachable peers is not stale because another is off.
- **confl**: `*.conflict-*` copies in all roots, scanned like `docsync conflicts` (includes honoured).
  **dumps**: `deadline-*.txt` in the log dir from the last 24 h. **icloud**: ok per root and age of
  the last `icloud-check` (`icloud-<node>.json`), when there is one.
- Every `sync`/`plan` writes an origin line into its `history.log` block (also after a deadline
  event): `origin: <argv> | parent <ppid> <parent command, max 200 chars> | agent=<yes|heal|no>`, the
  parent from `ps -o command= -p <ppid>` (`parent ?` when ps fails); `agent=yes` with `--from-agent`
  (the launchd agent), `agent=heal` with `--from-heal` (`docsync heal` passes it to its own
  `sync`/`plan`). A heal run counts for last ok / last success like any other run and raises no
  `non_agent_run`.
- Alerts come from one pure function, `docsync.monitor_alerts.alerts(node) -> [Alert]` (shared with
  `heal`). Each has a stable `code` and code-specific `data`; exit 0 without FAIL, 1 with at least one:

| code | level | when | data |
|---|---|---|---|
| `agent_not_loaded` | FAIL | launchctl does not know the agent | |
| `agent_exit` | FAIL | last exit not 0, 2 or 3, or 2 without a failed peer in the agent's own last run (newest `agent=yes` block; the last run when there is none) | `exit` |
| `agent_foreign` | FAIL | not `<python> -m docsync` with an existing, current docsync | `program` (argv before `sync`), `reason` |
| `stale_success` | FAIL | last synced run older than 2 h, or none after the never-synced grace | `age_min` (null: none) |
| `never_synced` | WARN | no synced run yet, agent loaded less than 2 × interval + 30 min (or load time unknown) | `loaded_age_min`, `grace_min` |
| `peer_failed` | WARN | failed peers in the last run (exit 2 is then expected) | `peers`, `reasons` (`peer [root]: reason`) |
| `agent_no_max_runtime` | WARN | no `--max-runtime`, or 0 | |
| `conflicts` | WARN | open conflict copies | `count`, `roots` {root: n} |
| `deadline_dump` | WARN | deadline dump within 24 h | `paths` |
| `peer_lock_lost` | WARN | `peer lock lost` in the last run | `count` |
| `held_deletes` | WARN | `N held back by deletion guard` in the last run | `count` (sum) |
| `non_agent_run` | WARN | last run's origin has `agent=no` (not `heal`) | `origin` |
| `icloud_incomplete` | WARN | last iCloud check not ok for a root | `roots` |
| `peer_unreachable` | WARN | node unreachable, in error or a local directory | `state` |
| `peer_too_old` | WARN | peer docsync without `monitor`, or reporting a version below 1.3.3 | `version` (its `--version`, or null) |

- Peers answer for themselves like `icloud-check`: `docsync monitor --local --json` over ssh, with
  `docsync` from the extended PATH or the Python next to `maccluster`. A docsync without the command
  shows `docsync too old on <peer> (needs 1.3.3)`. The alerts are computed here from each node's facts.
- Bounded to about 45 s: peers are probed in parallel, each finds a host by ping within 2 s (no
  Bonjour/ARP fallback), ssh gets `ConnectTimeout=5`, and a peer that does not answer within 45 s is
  `error: no answer within 45 s` (its whole ssh process group is killed). The conflict scan gets
  20 s; past that `conflicts` is null.
- `--interval SEC` refreshes until Ctrl-C (exit 130, no traceback); `--once` (the default) prints one
  report. `--json` prints one object per report:

```
{"schema": 1, "version": "…", "checked_at": <unix s>, "ok": bool, "exit_code": 0|1,
 "alerts": [{"node", "level": "FAIL"|"WARN", "code", "text", "data": {…}}],
 "nodes": [{"node", "state": "ok"|"unreachable"|"docsync too old on …"|"error: …", "version",
   "agent": {"loaded", "state", "last_exit", "program": [argv before "sync"], "program_ok",
             "interval_s", "max_runtime", "loaded_age_s", "version": agent's docsync|null,
             "foreign_reason": null|"shim"|"missing interpreter"|"old <v>"},
   "running": {"run_lock": <holder>|null, "root_locks": [<holder>]},
   "last_run": <run>|null, "last_success": <run>|null, "last_synced": <run>|null,
   "last_agent_run": <run>|null,
   "conflicts": {root: n}|null, "deadline_dumps": [paths],
   "icloud": {"checked_at", "age_s", "roots": {root: ok}}|null,
   "alerts": [{"level", "code", "text", "data"}]}]}
<holder> = {"pid", "started", "command", "host", "alive": true|false|null, "lock": file name}
<run>    = {"time": "YYYY-MM-DD HH:MM:SS", "age_s", "kind": "report"|"deadline", "ok", "failed", "busy",
            "files", "conflicts", "peer_lock_lost", "symlink_skipped", "held_deletes", "deadline_exceeded",
            "failed_peers": [{"peer", "root", "reason"}],
            "origin": {"text", "agent": true|false|null, "by": "agent"|"heal"|"other"|null}|null}
```

A node without facts (`state` other than `ok`) has `null` for every fact key. New keys may be
added; existing keys keep their meaning while `schema` is 1.

## Parallel peers

`docsync sync` with several peers runs them **in parallel**, one thread per peer, each with
its own connection, manifest and base. The terminal shows one progress line per peer; the
reports are printed together at the end. `--sequential` restores one-after-another,
`-j N` caps the number of concurrent peers. Two peers pulling the same new file at the same
moment is harmless (rsync writes a temp file and renames), the per-peer base keeps them apart.

## Speedtest

`docsync speedtest [peer] [--all-hosts] [--seconds N]` measures, per peer and host, the ping
latency, raw TCP throughput (a tiny sink/source server is started on the peer over ssh, no
iperf needed) and the throughput of the ssh path, which is what rsync is capped by. Upload and
download are timed separately. `--all-hosts` tests every reachable address of a peer, so you
can see Thunderbolt against Wi-Fi side by side.

## Progress and transport

While files move, a progress bar on stderr shows percent, KB done/total, current speed,
files done/total and rsync's ETA (`-q` or a non-TTY disables it). Hosts are probed
Thunderbolt-first like maccluster: addresses inside an active Thunderbolt Bridge subnet
(`bridge0`, `en1`, …) come first, then LAN names, then a Bonjour lookup, then Thunderbolt
ARP neighbours that answer `hostname -s` with a configured peer's name. Thunderbolt
addresses whose link is down are skipped without waiting for ping timeouts. The report
header says which transport was used (`via Thunderbolt`, `via LAN`, …).

`docsync tb` shows the Thunderbolt picture: every port with receptacle, link state and
negotiated speed (from `system_profiler`), the attached device, the bridge interfaces with
their IPv4 addresses, ARP neighbours on the bridge and whether each peer answers over it.
When a sync runs over Thunderbolt the report header carries the speed (`via Thunderbolt 40 Gb/s`).
It also draws a cabling sketch: this Mac and (over ssh) every peer with each Thunderbolt
receptacle, what is plugged there and at which speed, free receptacles marked `◀ plug here`,
and for a Mac mini / Mac Studio the front ports flagged as USB-C only. Without a link it adds a
wrong-vs-right sketch. Rule of thumb: on a Mac mini the cable goes into a **back** port, the two
front ports are USB 3 without Thunderbolt and never form a bridge. Attached Macs are matched to
maccluster nodes via their Thunderbolt Domain UUID (`tb_domain_uuids` in `cluster.toml`).
`--no-remote` skips the ssh round trip.

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
If none of a peer's `hosts` answers, docsync browses Bonjour for an `_ssh` instance with
the same base name and resolves it — macOS re-advertises Macs as `NAME-2930.local`,
`NAME-3000.local`, … after reboots, so static names alone are not enough.

## iCloud "Desktop & Documents"

If `~/Documents` is managed by iCloud, most files are *evicted* (dataless: logical size
on disk, no content). Reading them through rsync would download everything. docsync
therefore hides evicted files on **both** sides for the run: they are never pulled,
never counted as deleted, and their base entry survives. The report shows them as
`cloud-only`. `docsync doctor` warns when the folder is iCloud-managed; Macs on the same
Apple ID are already synced by iCloud and docsync only moves what iCloud has not.

`docsync icloud-check` shows whether iCloud really carries every root on every node:

```
node-c  documents  dd: on  not uploaded: 0  stuck: up 0 / down 0
node-c  desktop  dd: on  not uploaded: 0  stuck: up 0 / down 0
node-a  desktop  dd: off
node-b  documents  dd: off
iCloud: NOT complete — enable Desktop & Documents on node-a, node-b (System Settings › Apple ID › iCloud Drive)
```

- `dd` is on when the folder carries the `com.apple.file-provider-domain-id` attribute. A root that
  is on for some nodes and off for others fails; one that is off everywhere is `docsync only`.
- `not uploaded` counts files iCloud has not uploaded and that changed more than 15 min ago (a
  small Swift helper reads URL resource values, skipping excluded subtrees; built with `swiftc` on
  first use). `stuck` counts failed items in `brctl status` below that folder: `up` = uploads failing
  for more than 15 min (fails the check), `down` = evicted files that could not be downloaded
  (reported only, no data at risk). Output it does not understand is `unknown`.
- Parity, for roots on everywhere: the same paths on every node (a cloud-only file counts as
  present), the same size and an mtime within 2 s where two nodes hold a file locally, no iCloud
  duplicate `<name> 2.<ext>` of the same size next to `<name>.<ext>`. The first 20 problems are listed
  (`missing`, `differs`, `icloud duplicate`).
- Peers answer for themselves: `ssh <peer> sh -s` runs `docsync icloud-check --local --json` with
  `docsync` from the peer's PATH (extended by `/opt/homebrew/bin`, `/usr/local/bin`, `~/.local/bin`)
  or the Python next to `maccluster`. A docsync without the command or reporting a version below
  1.3.0 shows `docsync too old on <peer>`. Only metadata is read: no file contents, no downloads,
  no iCloud setting is changed.
- Exit 0 complete, 1 not. The result is saved per node in `~/.local/state/docsync/icloud-<node>.json`.
  When no host of a peer answers during a sync and the last check (at most 24 h old) passed that
  root on both sides, the peer reads `unreachable — covered by iCloud` and counts as ok: Thunderbolt
  first, then LAN, then iCloud.

## Install

```
make test
make install        # symlink ~/.local/bin/docsync → this checkout (development)
make wheel          # dist/docsync_lan-<version>-py3-none-any.whl
pipx install dist/docsync_lan-*.whl   # or: pipx inject maccluster dist/docsync_lan-*.whl
```

The distribution is called **docsync-lan** because `docsync` on PyPI is an unrelated project;
the command and the import name are `docsync`. It ships as a wheel on the GitHub release, not
on PyPI — never `pip install docsync`.

## Engine API

Programs that already know their peers (maccluster) call the engine directly:

```python
from docsync import api

res = api.sync(api.SyncRequest(
    roots=[api.RootSpec("documents", "~/Documents")],
    peers=[api.PeerSpec("node-b", "10.42.0.2", user="a321", bind_ip="10.42.0.3", via="Thunderbolt")],
    progress=lambda peer, root, phase, done, total: ...,
))
res.ok, res.exit_code, res.to_dict()   # exit_code: 0 ok, 2 a peer failed, 3 another run holds the lock
```

`api.resolve_peer(hosts, user)` exposes the Thunderbolt-first host detection. The engine never
prints and never exits the process; invalid requests raise `ValueError` before any network
access. `docsync sync --json` / `docsync plan --json` print the same `to_dict()` as the last
line. A host of `"/"` means "this machine" (the root's peer path is used); `/` itself is refused
as a peer folder.

## Files

- `docsync/engine.py` — the 3-way planner (pure, fully unit-tested)
- `docsync/manifest.py` — tree scan, excludes, remote lister
- `docsync/transport.py` — rsync/ssh and local-directory transports
- `docsync/session.py` — one run against one peer
- `docsync/report.py` — the final report
- `docsync/cli.py` — commands
- `docsync/monitor.py`, `docsync/monitor_alerts.py`, `docsync/monitor_report.py` — `docsync monitor` (facts of one Mac; alert codes; table and peers)
- `docsync/probe.py` — runs a docsync subcommand on a peer over ssh (icloud-check, monitor)
