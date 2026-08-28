# Roadmap 0.3 — Fabric-Muster aus exo, ohne exo-Abhängigkeit

| Feld | Wert |
|---|---|
| Projekt | MacCluster (`maccluster`) |
| Stand | 2026-08-16 |
| Ausgang | Produkt **0.2.9** (mesh health, RDMA RO, `--exo` opt-in, heal-watchdog) |
| Ziel | **0.3.0 → 0.3.2** — drei Wellen, jeweils einzeln releasbar |
| Auftrag | User 2026-08-16: konkrete Roadmap aus exo-Lektionen; **kein** Merge mit exo/meradOS |

Dieses Dokument ist die **verbindliche Umsetzungsplanung** für die nächste Produktlinie.
Es ersetzt nicht `BACKLOG.md` (v1-Wellen, abgeschlossen). Neue Arbeit startet hier.

---

## 1. Ziel in einem Satz

MacCluster bleibt das **Thunderbolt-Fabric-Produkt**. Es übernimmt aus dem exo-Betrieb nur Diagnostik- und Ops-Muster (Pfad erzwingen, Full-Mesh-Bench, koordinierter Heal, Host-Snapshot) — **exo bleibt optional, localhost, read-only**.

## 2. Harte Grenzen (nicht verhandelbar)

| Darf | Darf nicht |
|---|---|
| Inventar aus `cluster.toml` | UDP/mDNS-Discovery, Leader-Wahl |
| SSH nur über TB (`10.42.0.x` + `BindAddress`) | Wi-Fi/LAN als Default-Pfad |
| `rdma_ctl status` lesen | `rdma_ctl enable` (Recovery-OS) |
| `status --exo` / `doctor --exo` wie heute | EXO.app starten, Modelle, `/bench/chat/completions` |
| Generischer Busy-Hook (Datei + Env) | IBKR/`mos1:8080` hart verdrahten |
| `heal --fleet` = remote `maccluster heal` | exo-`launchctl kickstart`, Slack, `git pull` |

`schema_version` bleibt **1**. Kein `lan_ip`-Feld in dieser Linie (Ethernet-vs-TB-Vergleich wäre Scope-Creep).

## 3. Wellen und Versionen

| Welle | Version | CLI | Nutzen |
|---|---|---|---|
| **A** | **0.3.0** | `bench --mesh` + Busy-Guard | Full-Mesh TCP über TB, sequentiell, gebunden |
| **B** | **0.3.1** | `heal --fleet` | Koordinierter Ensure auf allen Membern |
| **C** | **0.3.2** | `doctor --host` `[--fleet]` | RAM/Load/Disk/Thermal ohne exo |

Abhängigkeit: A legt `services/fleet_exec.py` und den Busy-Guard; B und C nutzen denselben Hop.

---

## 4. Shared Foundation (in Welle A, von B/C wiederverwendet)

### 4.1 `FleetHop` — ein SSH-Lauf über die Bridge

**Neu:** `src/maccluster/services/fleet_exec.py`

Heute liegen gebundene SSH-Aufrufe in `speedtest_service.py` (`_try_start_remote_iperf`, `_reverse_iperf`). Dreimal kopieren wäre Drift. Ein Hop:

```python
@dataclass(frozen=True)
class FleetHopResult:
    node_id: str
    peer_ip: str
    ok: bool
    exit_code: int
    stdout: str
    stderr: str
    skipped: bool = False
    message: str = ""


def iter_peers(cfg, self_node, *, peer: str | None = None) -> tuple[Node, ...]:
    """Config order, never self, optional id/IP filter."""


def run_on_peer(
    ctx,
    *,
    self_ip: str,
    node: Node,
    remote: tuple[str, ...],
    timeout: float,
    connect_timeout: int = 8,
) -> FleetHopResult:
    """ssh_bind_argv → ProcessRunner. Never shell=True."""
```

Regeln:

- Ziel **muss** in `cfg.subnet` liegen (`require_cluster_ip`).
- Default **sequentiell** (iperf-Ports, Heal-Reihenfolge).
- `run_on_peers(..., parallel=False)` für Doctor-Host-Fan-out später `parallel=True` mit Timeout 3 s.
- Kein `sudo` im Helper; Aufrufer entscheidet den Remote-String.

**Tests:** `tests/unit/services/test_fleet_exec.py` — Fake ProcessRunner, Ablehnung von `192.168.1.1`, BindAddress in argv, Filter `--peer`.

### 4.2 Busy-Guard (Saturation)

**Neu:** `src/maccluster/services/busy_guard.py`

exo-netbench bricht ab, wenn Live-Trading die Bridge sättigen würde. MacCluster bleibt produktneutral:

1. Env `MACCLUSTER_BUSY` in `{1,true,yes,on}` (case-insensitive) → busy.
2. Datei `~/.config/maccluster/busy` existiert → busy. Erste Zeile (max 120 Zeichen, sanitized) = Reason.

```python
@dataclass(frozen=True)
class BusyState:
    busy: bool
    reason: str  # "" if idle


def read_busy_state(*, env: Mapping[str, str], busy_path: Path) -> BusyState: ...
```

- `bench --mesh` und `speedtest` (iperf-Zweig) rufen den Guard **vor** dem ersten iperf.
- Busy → Exit **3** (degraded), Message `fabric busy: <reason> — skip saturation`.
- `--force` übergeht den Guard (explizit, in Help erwähnt).
- meradOS **kann** die Datei schreiben; MacCluster liest sie nur. Kein HTTP zu `:8080`.

**Tests:** Env, Datei mit Reason, Datei leer, `--force`, Symlink-Datei ablehnen (wie Config: `lstat`, Exit 2).

### 4.3 BenchPort-Protokoll nachziehen

`ports/bench.py` deklariert heute `run(target, *, duration=5)` — der Adapter hat schon `bind_ip`. Protokoll und `FakeBench` auf `bind_ip: str | None = None` angleichen. Kein Verhaltenwechsel.

---

## 5. Welle A — `bench --mesh` (0.3.0)

### 5.1 Verhalten

```text
maccluster bench --mesh
maccluster bench --mesh --duration 5
maccluster bench --mesh --peer node-b
maccluster bench --mesh --force          # ignore busy
maccluster bench 10.42.0.2               # unverändert: ein Ziel
```

| Fall | Aktion |
|---|---|
| `--mesh` ohne SSH zu einem Peer | Nur **Self → Peer** (wie heutiges `speedtest`, gebunden an Self-IP). Warnung `orchestrate skipped: no ssh`. |
| `--mesh` und SSH ok | **Gerichteter Full-Mesh** über TB: jedes Paar `(src, dst)`, `src ≠ dst`, **sequentiell**. Remote-Server: `iperf3 -s -1 -B <dst-TB-IP>`. Client (via SSH auf src oder lokal wenn src=self): `iperf3 -c <dst> -B <src-TB-IP> -t N -J`. |
| Peer-Firewall blockt inbound | Bestehenden Reverse-Pfad aus `speedtest_service` wiederverwenden (Peer als Client). |
| Parallel | **verboten** — Port 5201 kollidiert (exo-netbench-Lektion). |
| Default-Route / Ethernet | **nicht** messen in 0.3. |

### 5.2 Modelle

**Erweitern** `domain/models.py`:

```python
@dataclass(frozen=True)
class MeshPathResult:
    src_id: str
    dst_id: str
    src_ip: str
    dst_ip: str
    mbps: float | None
    retransmits: int | None
    quality: BenchQuality
    flags: tuple[str, ...]
    ok: bool
    message: str
    reverse: bool = False  # used firewall fallback


@dataclass(frozen=True)
class MeshBenchReport:
    bind_mode: str  # "tb-bridge"
    duration_s: int
    orchestrated: bool
    busy_skipped: bool
    paths: tuple[MeshPathResult, ...]
    summary: str
```

Schwellen bleiben die Konstanten in `constants.py`:

| Qualität | Mbit/s | Bedeutung |
|---|---|---|
| excellent | ≥ 30_000 | gesunder TB-TCP (Ziel ~38 Gbit/s) |
| good | ≥ 1_000 | nutzbar, unter TB-Ideal |
| marginal | ≥ 100 | eher Ethernet/Stau |
| poor | < 100 | kaputt |

Zusätzliches Flag wenn `ok` und `mbps < 15_000`: `below_tb_tcp_floor` (exo-netbench: TB &lt; 30 Gbit/s verdächtig; wir warnen ab 15 Gbit/s, um 20G-Kabel nicht falsch-rot zu färben).

Exit:

- 0 — alle gemessenen Pfade `ok` und quality ≠ poor
- 1 — iperf3 fehlt lokal, oder alle Pfade hart fehlgeschlagen
- 2 — `--mesh` und einzelnes `target` gleichzeitig; ungültiges `--peer`
- 3 — busy (ohne `--force`); oder mindestens ein Peer down / quality=poor bei sonst grünem Lauf

### 5.3 Dateien

| Aktion | Pfad |
|---|---|
| Neu | `src/maccluster/services/fleet_exec.py` |
| Neu | `src/maccluster/services/busy_guard.py` |
| Neu | `src/maccluster/services/mesh_bench_service.py` |
| Neu | `tests/unit/services/test_fleet_exec.py` |
| Neu | `tests/unit/services/test_busy_guard.py` |
| Neu | `tests/unit/services/test_mesh_bench_service.py` |
| Neu | `tests/unit/commands/test_bench_mesh.py` |
| Ändern | `src/maccluster/ports/bench.py` (`bind_ip`) |
| Ändern | `src/maccluster/cli/parser.py` (`--mesh`, `--force` an `bench`) |
| Ändern | `src/maccluster/commands/bench.py` |
| Ändern | `src/maccluster/domain/models.py` |
| Ändern | `src/maccluster/render/plain.py` + `json_out` (Mesh-Tabelle) |
| Ändern | `src/maccluster/services/speedtest_service.py` — Guard vor iperf; SSH über `fleet_exec` ziehen soweit ohne Verhaltenbruch |
| Ändern | `README.md` Commands-Tabelle, `docs/faq/operator.md` |
| Ändern | `CHANGELOG.md`, `__version__ = "0.3.0"` |

`mesh_bench_service` orchestriert; `Iperf3Bench.run` bleibt der lokale Client. Remote-Client/Server nur über `fleet_exec` + erlaubtes `iperf3`.

### 5.4 Render (Plain)

```text
mesh bench  Δ5s  path=tb-bridge  orchestrated=yes
  node-a → node-b  37120 Mbit/s  quality=excellent  retransmits=0
  node-b → node-a  36890 Mbit/s  quality=excellent  retransmits=0
summary: 2/2 ok
```

`--json`: Envelope `command=bench`, `schema_version=1`, Report als Objekt.

### 5.5 Akzeptanz

- [ ] `bench 10.42.0.2` unverändert grün (Regression).
- [ ] `bench --mesh` ohne Config-Peers → Exit 2, klare Message.
- [ ] Fake-Bench: 2-Node-Config, Self→Peer ein Path, `orchestrated=False`.
- [ ] Fake-SSH: 2-Node, beide Richtungen, Server-argv enthält `-B <dst>`.
- [ ] `MACCLUSTER_BUSY=1 bench --mesh` → Exit 3, kein `iperf3` im Runner-Log.
- [ ] `bench --mesh --force` bei Busy → läuft.
- [ ] `make verify` grün.

---

## 6. Welle B — `heal --fleet` (0.3.1)

### 6.1 Verhalten

```text
maccluster heal --fleet
maccluster heal --fleet --dry-run
maccluster heal --fleet --peer node-b
maccluster heal --fleet --together
```

Ablauf (exo-Lektion „sync restart“, aber **nur** MacCluster):

1. **Self zuerst** — bestehendes `ensure_local` (gleiche Privilege-Semantik wie `heal`).
2. Pause **2 s** (Bridge darf sich setzen).
3. Jeder Peer **in Config-Reihenfolge**, sequentiell:  
   `ssh … 'export PATH="$HOME/.local/bin:/opt/homebrew/bin:$PATH"; command -v maccluster && maccluster heal'`
4. Fehlt Remote-CLI → Hop `skipped`, Message `maccluster not on peer — remote-install`.
5. Remote-Stdout enthält `admin/sudo required` → Hop nicht ok, **kein** Passwort, Hinweis `run sudo maccluster heal on <id>`.
6. `--together` nach Schritt 3: auf Self + erreichbaren Peers `launchctl kickstart gui/$(id -u)/com.maccluster.heal` (Label aus `constants.LAUNCH_AGENT_LABEL`). **Nicht** exo-Labels.
7. `--dry-run`: Self-Plan (`ensure_local(dry_run=True)` falls vorhanden, sonst nur print) + Liste der Hops, Exit 0.

Nicht kombinieren: `--fleet` + `--loop` / `--watchdog` → Exit 2.

### 6.2 Modell

```python
@dataclass(frozen=True)
class FleetHealReport:
    self_result: MutateResult | None
    hops: tuple[FleetHopResult, ...]
    together: bool
    summary: str
```

Exit: 0 alle ok/skipped-noop; 1 Self-Privilege-Fail; 2 Usage; 3 Self ok, mindestens ein Peer fail/skipped-missing.

Audit: eine Zeile pro Hop in `actions.log` wenn Audit an (`heal-fleet`, node id, result).

### 6.3 Dateien

| Aktion | Pfad |
|---|---|
| Neu | `src/maccluster/services/fleet_heal_service.py` |
| Neu | `tests/unit/services/test_fleet_heal_service.py` |
| Neu | `tests/unit/commands/test_heal_fleet.py` |
| Ändern | `cli/parser.py` (`--fleet`, `--together`; `--dry-run` schon bei `up`) |
| Ändern | `commands/heal.py` |
| Ändern | `domain/models.py` |
| Ändern | `render/plain.py` |
| Ändern | README, operator FAQ, CHANGELOG, `__version__ = "0.3.1"` |

`mutate_service.ensure_local` bleibt der einzige Mutator auf Self. Fleet **mutiert Peers nur**, indem es deren eigene CLI aufruft (symmetrisch, kein Remote-`ifconfig`).

### 6.4 Akzeptanz

- [ ] `heal` ohne Flags unverändert.
- [ ] `--fleet --loop` → Exit 2.
- [ ] Dry-run: kein `net_apply`, SSH-argv sichtbar im verbose, Exit 0.
- [ ] Peer ohne `maccluster` im Fake-stdout `command not found` → skipped, Exit 3.
- [ ] `--together` kickstartet nur `com.maccluster.heal`, nie einen exo-Label-String.
- [ ] `make verify` grün.

---

## 7. Welle C — `doctor --host` (0.3.2)

### 7.1 Verhalten

```text
maccluster doctor --host
maccluster doctor --host --fleet
maccluster doctor --host --fleet --peer node-b
```

Ohne `--host`: Doctor **unverändert** (kein Pflicht-SSH, kein vm_stat — Status-Budget bleibt).

**Lokal** (`--host`):

| Check-ID | Quelle | Severity |
|---|---|---|
| `host` | RAM used/free, load 1m | INFO; WARN nur wenn Parse fail |
| `disk` | `df -P /` free | WARN wenn free &lt; 20 GiB |
| `thermal` | `pmset -g therm` | WARN wenn `CPU_Speed_Limit` vorhanden und &lt; 100 |
| `ntp` | `sntp -d time.apple.com` optional | SKIPPED wenn Binary fehlt; WARN wenn \|offset\| &gt; 2 s |

**RAM:** Page size aus `vm_stat`-Header (`Mach Virtual Memory Statistics: (page size of N bytes)`), nie `*4`. used = (active+wired)*pagesize; free = (free+inactive)*pagesize.

**`--fleet`:** derselbe Snapshot pro Peer über `fleet_exec`, Remote druckt **eine JSON-Zeile** (kleines Python oder `printf`), lokal geparst. Timeout 4 s. Unreachable → Finding `host:<id>` WARN, Doctor-Exit 3.

`host` / `disk` / `thermal` in `_CLUSTER_WARN_IDS` aufnehmen (nur diese; `ntp` SKIPPED bleibt Exit 0).

### 7.2 Modelle + Adapter

```python
@dataclass(frozen=True)
class HostSnapshot:
    node_id: str
    ram_used_gb: float | None
    ram_free_gb: float | None
    load_1m: float | None
    disk_free_gb: float | None
    cpu_speed_limit_pct: int | None  # None = not reported
    ntp_offset_s: float | None
    error: str | None = None
```

**Neu:** `src/maccluster/adapters/host_macos.py` — nur ProcessRunner, Allowlist-Erweiterung:

```
vm_stat, df, uptime, pmset, sntp
```

`sntp` liegt unter `/usr/bin` oder `/usr/sbin` — Search-Paths reichen. Parser **pure** in `src/maccluster/doctor_logic/host_parse.py` (vm_stat-Fixtures wie TB-Parser).

### 7.3 Dateien

| Aktion | Pfad |
|---|---|
| Neu | `src/maccluster/adapters/host_macos.py` |
| Neu | `src/maccluster/doctor_logic/host_parse.py` |
| Neu | `src/maccluster/ports/host.py` |
| Neu | `tests/unit/doctor_logic/test_host_parse.py` |
| Neu | `tests/unit/services/test_doctor_host.py` |
| Neu | `tests/fixtures/vm_stat/apple_silicon_16k.txt` |
| Ändern | `constants.py` Allowlist |
| Ändern | `app_factory.py` Host-Port verdrahten |
| Ändern | `doctor_logic/checks.py` + `report.py` |
| Ändern | `services/doctor_service.py` |
| Ändern | `commands/doctor.py`, `cli/parser.py` |
| Ändern | README, operator FAQ, CHANGELOG, `__version__ = "0.3.2"` |

### 7.4 Akzeptanz

- [ ] `doctor` ohne `--host` erzeugt **keine** `vm_stat`-argv (Regression, Budget).
- [ ] Fixture 16K-Pages: 1000 active + 1000 wired → used = 1000\*16384/1024³ GB, nicht 4× zu klein.
- [ ] `CPU_Speed_Limit = 80` → thermal WARN, Exit 3.
- [ ] `df` 10 GiB free → disk WARN.
- [ ] `--host --fleet` mit einem down Peer → WARN, restliche Snapshots da.
- [ ] `make verify` grün.

---

## 8. Bewusst später / nicht in 0.3

| Idee | Warum warten |
|---|---|
| Ethernet-vs-TB-Matrix | braucht `lan_ip` in Config = Schema-Diskussion |
| `status --exo` Fan-out auf alle TB-IPs | nützlich, aber Correlator ist schon korrekt lokal; kein Blocker |
| `monitor` Host-Zeile | erst nach C, sonst Status-Loop &gt; 3 s |
| Inference-`generation_tps` | exo-Workload, Out-of-Scope |
| Automatisches Schreiben von `busy` aus IBKR | meradOS-Hook, nicht dieses Repo |

---

## 9. Implementierungsreihenfolge (Commits)

Welle A (0.3.0):

1. `test: fleet hop rejects non-cluster IPs`
2. `feat: fleet_exec SSH helper bound to TB self-ip`
3. `test: busy guard env and file`
4. `feat: fabric busy guard for saturation benches`
5. `fix: BenchPort accepts bind_ip`
6. `test: mesh bench self-to-peers and orchestrated pairs`
7. `feat: bench --mesh sequential TB full mesh`
8. `docs: bench --mesh and busy file`
9. `chore: version 0.3.0`

Welle B (0.3.1): analog test→feat→docs→version.

Welle C (0.3.2): Parser-Fixtures zuerst (TDD), dann Adapter, dann Flag.

Ein Writer zur Zeit im Worktree. Kein Auto-Push.

---

## 10. Verify pro Welle

```bash
cd ~/Developer/fabrik/projects/maccluster
python3 -m pip install -e ".[dev]"
make verify
```

Live (optional, nicht CI):

```bash
export PATH="$HOME/.local/bin:$PATH"
maccluster bench --mesh          # A
maccluster heal --fleet --dry-run
sudo maccluster heal --fleet     # B, nur mit SSH+Admin
maccluster doctor --host
maccluster doctor --host --fleet # C
```

---

## 11. Traceability exo-Lektion → Welle

| exo-Lektion | Welle | MacCluster-Form |
|---|---|---|
| `iperf3 -B` beide Enden, nie Default-Route | A | Mesh nur `10.42.0.x` |
| Sequential, Port nicht teilen | A | ein Paar nach dem anderen |
| TB-TCP-Schwelle ~38 Gbit/s | A | excellent ≥ 30 Gbit/s, Flag &lt; 15 |
| Workstation orchestriert, misst nicht selbst ins LAN | A | `--mesh` orchestriert über TB-SSH |
| Trading-Guard | A | Datei + Env, kein IBKR |
| Sync-Restart nach Mesh-Kollaps | B | `--fleet` + optional `--together` |
| launchd KeepAlive ≠ Hang | schon 0.2.4 | Watchdog bleibt; Fleet startet Heal-Agent neu |
| HTTP-alive ≠ mesh-alive | schon 0.2.4 | unverändert |
| 16K Pages / RAM-Triage | C | `doctor --host` |
| Thermal / NTP | C | optionale Findings |
| Skill-Sync, Slack-Healer, EXO.app | — | **nein** |

---

*Ende ROADMAP-0.3-fabric.md — nächster Schritt: Welle A implementieren nach Freigabe.*
