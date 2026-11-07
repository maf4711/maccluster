# docsync

Terminal tool that keeps `~/Documents` identical on several Macs over the LAN.
Bidirectional, rsync+ssh, no cloud, no daemon required. Python 3.11+ stdlib only.

```
docsync init --from-maccluster      # peers from ~/.config/maccluster/cluster.toml
docsync doctor                      # rsync, ssh keys, peer reachability
docsync plan                        # dry run: what would move
docsync sync                        # do it, then print the report
docsync tb                          # Thunderbolt ports, link, speed, cabling sketch
docsync speedtest --all-hosts       # raw TCP + ssh throughput per peer and host (TB vs LAN)
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

**Case-insensitive paths.** APFS treats `Gesundheit/Befunde` and `Gesundheit/befunde` as the
same folder, so docsync compares paths ignoring case (`case_insensitive = true`) while using
each side's own spelling for rsync. Without this, a case-only difference between two Macs
looks like "deleted on the peer" and would wipe the folder locally.

**Deletion guard.** More than `max_delete` deletions in one run (default 25) are held back
and reported; review with `docsync plan -v` and rerun with `docsync sync --force-deletes`
if they are intended. A peer mid-reboot, a renamed folder or an unmounted volume must never
turn into a mass delete.

## Unattended runs

- Transport failures (ssh banner timeout, peer rebooting, link flap, rsync timeout) are retried
  up to three times with 5/15/45 s delays; the report shows `retries N`.
- `docsync sync --wait SEC` waits for a running sync instead of exiting with code 3.
- `docsync watch` backs off exponentially after a failed run (interval → 2× … 6×) and keeps
  running on unexpected errors, which it writes to `history.log`.
- After a run that failed, produced a conflict copy or held deletions back, a Notification
  Center message appears (`--no-notify` to silence).
- `docsync install-agent` adds a launchd `WatchPaths` trigger on the Documents folder next to the
  interval (`--no-watch` for interval only). With `skip_on_battery = true` in the config the
  agent's runs do nothing on battery power.

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
