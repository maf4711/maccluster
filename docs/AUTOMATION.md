# Automated cluster maintenance

MacCluster stays in Python. `automation` combines package updates, peer installation,
healing, service management and diagnostics into one unattended workflow. The
optional schedule runs once a day by default; manual and scheduled runs share a
lock so they cannot update the same local installation concurrently.

## Run once

```bash
maccluster --json automation run --dry-run
maccluster --json automation run
```

The default scope is this Mac and every configured peer. A real run validates the
inventory, heals this Mac, ensures its heal/watchdog services, builds one wheel,
installs that exact wheel on each peer, runs diagnostics and updates this Mac last.
Peers are updated sequentially in configuration order. If a peer installation or
its health checks fail, later peer updates, optional file sync and the local
package update are skipped. Local diagnostics still run, and the receipt identifies
the peer that stopped the rollout. A later run retries from configuration order;
verified unchanged installations can be reused. Peers already updated are not
rolled back as a group. Dry-run plans show this success condition.

```bash
maccluster automation run --peer node-b
maccluster automation run --local-only
maccluster automation run --no-update
maccluster automation run --source /absolute/path/maccluster.whl
```

`--no-update` skips package builds and installation, while still healing existing
peers and checking local services and health. In this mode, one failed peer does
not prevent healing attempts on remaining peers. `--source` accepts a local wheel,
source checkout or HTTPS archive. The default is this repository's `main` archive
on GitHub. Remote operations use the configured Thunderbolt addresses.

## Schedule

### Pin an approved release wheel

Use a locally available release wheel and its independently approved SHA-256:

```bash
maccluster update --source /absolute/path/maccluster-0.5.0-py3-none-any.whl --sha256 <64-hex-digest>
maccluster automation install --source /absolute/path/maccluster-0.5.0-py3-none-any.whl --sha256 <64-hex-digest>
```

Replace the digest placeholder with the release's expected checksum. The wheel is
copied into private staging and that copy is inspected and hashed without invoking
a build backend or pip. A mismatch blocks local and peer package installation.
Each peer subsequently verifies the transferred wheel against this same digest.
The saved schedule retains both the absolute source path and checksum; changing
the file contents does not silently approve a different release. Keep the source
wheel available for later scheduled runs. The checksum proves equality with the
approved artifact, not who published it.

`--sha256` supports local wheels only and cannot be combined with `--no-update`.
Without this option, existing source-build behavior is retained, including the
default `main` archive. Older saved schedules remain readable. A dry run validates
the option format and reports the expected digest; it does not hash or install the
artifact. Normal fleet maintenance still performs its local heal/service steps
before staging the wheel; hash failure prevents package updates but is not a
transactional rollback of those independent maintenance steps.

### Local package recovery

Local updates retain exact wheel bytes under
`~/.local/share/maccluster/update-wheels/<sha256>/`. The successful installation
receipt records the wheel filename. Before a later update, recovery is eligible
only when the previous receipt, retained wheel, active CLI version and installed
wheel provenance agree for the same installation and package manager.

If installation exits with an error or its CLI version check fails, the updater
reinstalls that verified previous wheel using the original pipx or venv command.
Recovery verifies both version and wheel provenance. The update still returns a
failure, explicitly stating whether restoration succeeded; the previous success
receipt is preserved. A corrupt retained predecessor blocks installation before
the package manager runs.

The first update, legacy receipts without a retained wheel, missing recovery
files, and externally replaced installations may have no eligible predecessor.
There is no automatic recovery after an installer timeout: descendant processes
may still be writing. This is package recovery, not atomic environment switching
or restoration of configuration, dependencies, services or synced files. Remote
bootstrap does not yet perform this recovery. Retained wheels are not automatically
pruned. Full post-update cluster health checks and atomic environment replacement
remain future work.

### Install the schedule

```bash
maccluster automation install                     # every 86400 seconds
maccluster automation install --interval 3600      # hourly; minimum 300 seconds
maccluster --json automation status
maccluster automation uninstall
```

Options such as `--peer`, `--source`, `--no-update` and `--sync` can also be passed
to `automation install`. They are saved in `automation.json` beside the resolved
cluster configuration, with mode `0600`. Relative source paths become absolute.
The LaunchAgent invokes `automation run --saved`; it does not depend on the shell
that installed it. Installation registers the schedule without immediately running
maintenance. Run `automation run --saved` to execute it now, or add `--dry-run` to
inspect the saved plan.

Only one Mac should coordinate scheduled fleet updates. On other Macs use the
existing heal/watchdog services, or schedule `--local-only --no-update` checks.
This avoids different coordinators attempting peer package installations together.
Peer bootstrap now acquires the same user-local `automation.lock` as local
maintenance before installing packages or changing configuration. A competing
run fails after a bounded wait and can retry later. The kernel releases the lock
when its final owning process exits; the lock file is deliberately retained.
This protection applies when the coordinator sends the updated bootstrap script;
older coordinators must be updated before relying on fleet-wide exclusion.

The schedule is a **user LaunchAgent** and requires that user's login session.
`status` distinguishes an installed, loaded, idle job from one currently running.
Unchanged loaded services are reused without restarting them.

## Optional file sync

```bash
maccluster automation run --sync
maccluster automation install --sync --interval 3600
```

`--sync` enables home synchronization with SafetyNet backups and verification;
the home presets include `Developer`. It can be limited with `--peer`. It does not
run the development publishing/TestFlight workflow. Use the existing standalone
`sync`, `bench`, `speedtest`, `doctor` and `status` commands when only those operations
are needed; the existing `service sync-install` schedule remains available.

## Installation and updates

```bash
bash install.sh                                  # install locally
bash install.sh --automate                        # install, then maintain fleet
bash install.sh --schedule --interval 86400       # also register recurring runs
maccluster --json update --dry-run
maccluster update --source /absolute/path/maccluster.whl
maccluster remote-install node-b --no-config
```

The installer uses pipx when available, otherwise a dedicated virtual environment
under `~/.local/share/maccluster/venv`. Updates target the active installation;
they do not install into system Python. Wheel metadata and SHA-256 are checked,
and the installed CLI version is verified before success is recorded. An identical
wheel can be skipped only when its prior receipt and installed version agree.
Different wheel contents are reinstalled even if the version number is unchanged.

Automated peer installation seeds missing configuration and preserves an existing
peer configuration. Standalone `remote-install --no-config` does not upload or
replace configuration. Its dry run performs no SSH configuration writes, builds,
network probes or remote commands.

## Results and prerequisites

All run/update steps return structured JSON with `--json`. Exit codes are `0` for
success or a valid plan, `1` for failed steps, `2` for invalid options, and `3` for
degraded health without other failures. The last receipt is written atomically to
`~/Library/Caches/maccluster/automation-last.json`. Schedule output goes to
`~/Library/Logs/maccluster/automation.log` and `automation.err`.

Package-manager input is disabled, SSH uses key authentication, and commands have
timeouts. Remote installation uses a private staging directory, verifies the
transferred wheel and cleans up staging after errors. Later scheduled runs retry
failed work; completed package installations have receipts.

A valid shared cluster inventory, reachable bridge addresses and authorized SSH
access are prerequisites. Network changes still require existing administrative
rights; peer bootstrap uses `sudo -n` when needed. Automation reports missing access
instead of waiting for passwords. FileVault pre-login and enabling Remote Login
remain machine setup requirements. This change does not enroll machines or grant
new administrative rights.
