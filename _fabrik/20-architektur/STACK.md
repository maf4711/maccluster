# STACK — MacCluster

| Field | Value |
|---|---|
| Project | MacCluster (`maccluster`) |
| Phase | 2 ARCHITECTURE |
| Date | 2026-08-01 |
| Status | **Binding** |
| Source | Brief G, `ANFORDERUNGEN.md`, `ARCHITEKTUR.md`, ADRs |

Product code and identifiers: English. This file documents concrete technology choices with versions, rationale, and licenses.

---

## 1. Summary (one line)

**Python 3.11+ · argparse · tomllib · dataclasses · subprocess argv-only · optional rich · pytest · ruff · hatchling · no DB · no server · macOS Apple Silicon only**

---

## 2. Runtime stack

| Layer | Technology | Version / constraint | Required | License | Rationale |
|---|---|---|---|---|---|
| Language | **Python** | **≥ 3.11** (`requires-python = ">=3.11"`) | yes | PSF | Brief G; NFA-041; `tomllib`, modern typing |
| Packaging metadata | **pyproject.toml** PEP 621 | — | yes | — | Standard install via pip/pipx |
| Build backend | **hatchling** | ≥ 1.25 | yes (build) | MIT | Fast, simple, no setuptools legacy; alternatives acceptable if same entry points |
| Console entry | `maccluster = maccluster.cli.main:main` | — | yes | — | Single binary name on all members (A-034) |
| CLI parsing | **argparse** (stdlib) | 3.11+ | yes | PSF | ADR-0001; zero runtime dep; subcommands explicit |
| Config read | **tomllib** (stdlib) | 3.11+ | yes | PSF | ADR-0002; TOML is config truth |
| Config write | **handwritten / template emitter** | — | yes | — | Schema v1 small; avoids extra TOML writer dep |
| Domain models | **dataclasses**, **enum**, **typing**, **ipaddress** | stdlib | yes | PSF | No Pydantic (cold start NFA-007, dep surface NFA-025) |
| JSON output | **json** (stdlib) | — | yes | PSF | A-033; `schema_version` field |
| Subprocess | **subprocess.run** via `adapters/process.py` | `shell=False`, timeouts | yes | PSF | A-044 / NFA-022 |
| File lock | **fcntl** / exclusive create (stdlib) | macOS | yes | PSF | A-031 single-writer |
| Paths / FS | **pathlib**, **os**, **tempfile** | stdlib | yes | PSF | Atomic replace, 0600 |
| Optional TUI | **rich** | **≥ 13.7, < 15** (extra `[monitor]`) | no | **MIT** | A-037 Kann; lazy import; core works without it (NFA-033) |

### 2.1 Runtime dependency policy

```text
install_requires: []                    # pure stdlib runtime
optional-dependencies:
  monitor: ["rich>=13.7,<15"]
```

- **No** Click, Typer, Pydantic, HTTP frameworks, ORMs, async frameworks, cloud SDKs.
- **No** OpenAI / Gemini / LiteLLM / Ollama packages (Fabrik LLM policy; product has no LLM).

---

## 3. Host OS tools (not Python packages)

| Tool | Required | Role | Notes |
|---|---|---|---|
| `system_profiler` | yes (macOS) | Thunderbolt / hardware identity | Primary TB probe (ADR-0003) |
| `ioreg` | yes (macOS) | TB fallback / details | Fallback chain |
| `ifconfig` | yes | Bridge / IP read-write | Allowlisted mutator only |
| `networksetup` | yes | Network prefs where needed | Allowlisted |
| `ping` | yes | Peer reachability | Timeout ≤ 2 s |
| `launchctl` | yes (service) | LaunchAgent bootstrap/bootout | User domain AD-4 |
| `sw_vers` / `sysctl` / `scutil` | yes | Platform / hostname | Platform guard, identity |
| `iperf3` | no | `bench` only | Exit 1 if missing (A-026) |
| `ssh` | no | Optional peer probes | Default off (AD-2) |

These tools ship with macOS (except `iperf3` / optional Homebrew). They are **not** vendored.

---

## 4. Development / CI stack

| Tool | Version / constraint | License | Role |
|---|---|---|---|
| **pytest** | ≥ 8.0 | MIT | Unit + integration tests |
| **ruff** | ≥ 0.6 | MIT | Lint + format (single tool) |
| **pip-audit** | latest (CI/verify optional) | Apache-2.0 | SCA (NFA-025) |
| **GitHub Actions** | `macos-latest` and/or `ubuntu-latest` | — | Lint + pytest; no live TB farm |
| **Dependabot** | pip ecosystem | — | `.github/dependabot.yml` (QUALITAET) |

Optional (not required for v1 DoD):

| Tool | When | License |
|---|---|---|
| mypy | Best-effort on `domain/` + `ports/` | MIT |
| import-linter | Only if import-rule violations become chronic | MIT |

### 4.1 Verify chain (G5)

```bash
make verify
# equivalent:
ruff check src tests
ruff format --check src tests
pytest -q
```

Documented in product README.

---

## 5. Packaging & distribution

| Aspect | Choice | Rationale |
|---|---|---|
| Layout | `src/maccluster/` | Clean imports; editable install |
| Install paths | `pipx install .` / `pip install -e .` / `install.sh` | NFA-043; README primary path |
| Lockfile | `uv.lock` **or** pinned `requirements-dev.txt` + lock | QUALITAET G2 — one format in W1 |
| Product license | **MIT** | QUALITAET G1; max reuse for operator tooling |
| Platform classifiers | macOS, arm64 (Apple Silicon) | NFA-040; mutate guard elsewhere |
| Python classifiers | 3.11, 3.12, 3.13+ as available | NFA-041 |

---

## 6. Persistence & runtime paths

| Artifact | Path | Format |
|---|---|---|
| Cluster config | `~/.config/maccluster/cluster.toml` | TOML, `schema_version = 1` |
| Config override | `--config` > `MACCLUSTER_CONFIG` > default | ADR-0002 |
| Mutate lock | `~/.config/maccluster/mutate.lock` | PID + timestamp |
| LaunchAgent | `~/Library/LaunchAgents/com.maccluster.heal.plist` | XML plist |
| Action log (opt-in) | `~/.local/state/maccluster/actions.log` | append; rotate max 5 MiB |

**No database. No cloud storage. No telemetry endpoints.**

---

## 7. Explicit non-choices

| Rejected | Why |
|---|---|
| Typer / Click | Extra deps; argparse sufficient (ADR-0001) |
| Pydantic | Cold start + dep surface; schema tiny |
| FastAPI / HTTP API | Out of scope |
| SQLite / any DB | Config file is truth |
| asyncio framework | 4 nodes; sync + timeouts enough |
| PyObjC / Swift bridge | Brief Python; OS CLIs adequate |
| Root privileged helper daemon | Deferred; User LaunchAgent v1 (ADR-0005) |
| Dynamic third-party plugins | Static dispatch only |
| Docker as runtime | Needs host Thunderbolt network |

---

## 8. License compliance summary

| Component | License | Compatible |
|---|---|---|
| MacCluster product | MIT | yes |
| Python stdlib | PSF | yes |
| hatchling | MIT | yes |
| rich (optional) | MIT | yes |
| pytest | MIT | yes |
| ruff | MIT | yes |
| pip-audit | Apache-2.0 | yes |

No GPL/AGPL runtime dependencies. SCA gate: no open critical/high CVEs at acceptance (NFA-025).

---

## 9. Version pins (implementation target)

Exact pins land in lockfile during Welle 1. Targets:

```text
Python:            >=3.11
rich (optional):   >=13.7,<15
pytest:            >=8.0
ruff:              >=0.6
hatchling:         >=1.25
```

Verify current stable versions at implement time; do not introduce exotic pre-releases.

---

## 10. Assumptions

| ID | Assumption |
|---|---|
| S-1 | Target Mac minis have Python 3.11+ (system or pyenv/homebrew) |
| S-2 | Operators may install via pipx; PATH contains `maccluster` after install |
| S-3 | `iperf3` and SSH are optional host tools, not Python deps |
| S-4 | hatchling may be swapped for setuptools if packaging friction appears — entry point name stays `maccluster` |
