"""`docsync monitor`: this Mac and every peer in one table, then the alerts.

Peers answer for themselves (`monitor --local --json` over ssh, see docsync.probe); the alerts
(docsync.monitor_alerts) are computed here from each node's facts, so every node is judged by the
same rules. Exit 0 without FAIL, 1 with. Read-only like monitor.local_node."""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeout

from docsync import monitor, probe
from docsync.monitor_alerts import alerts, fmt_age

SCHEMA = 1
MIN_PEER_VERSION = "1.3.3"
PROBE_TIMEOUT = 45   # per peer; peers and this Mac run in parallel
TOTAL_BUDGET = 45.0  # the whole report: what is not done by then is reported, not waited for
CONNECT_TIMEOUT = 5
REACH_BUDGET = 2.0  # seconds to find a host that answers a ping
# On failure the peer also prints its `docsync --version` to stderr (for peer_too_old's data.version).
PEER_ARGS = 'monitor --local --json || { rc=$?; "$@" --version >&2; exit $rc; }'
NODE_KEYS = ("agent", "running", "last_run", "last_success", "conflicts", "deadline_dumps", "icloud")


def stub_node(name: str, state: str, version: str | None = None) -> dict:
    """Row of a node that gave no facts (unreachable, too old, error)."""
    return {"node": name, "state": state, "version": version, **{k: None for k in NODE_KEYS}, "alerts": []}


def probe_peer(p, ssh_cmd=None, timeout: float | None = None) -> dict:
    """The peer's own `monitor --local --json`, renamed to the peer's name here."""
    timeout = PROBE_TIMEOUT if timeout is None else timeout
    if p.host is None:
        return stub_node(p.name, "unreachable")
    t = probe.transport(p, next(iter(p.paths.values()), "~"), ssh_cmd)
    if not hasattr(t, "ssh"):
        return stub_node(p.name, "local directory — no agent to monitor")
    try:
        res = probe.run(t, PEER_ARGS, timeout=timeout, connect_timeout=CONNECT_TIMEOUT)
    except subprocess.TimeoutExpired:
        return stub_node(p.name, f"error: no answer within {timeout:g} s")
    if probe.unknown_command(res):
        found = re.search(r"^docsync (\S+)$", res.stderr, re.M)  # `docsync --version`, run after the failure
        return stub_node(p.name, f"docsync too old on {p.name} (needs {MIN_PEER_VERSION})", found and found.group(1))
    try:
        node = dict(probe.last_json(res.stdout)["nodes"][0])
    except (ValueError, KeyError, IndexError, TypeError):
        return stub_node(p.name, "unreachable" if res.returncode == 255 else f"error: {probe.error_text(res)}")
    if probe.version(node.get("version")) < probe.version(MIN_PEER_VERSION):  # a dev build below the floor
        return stub_node(p.name, f"docsync too old on {p.name} (needs {MIN_PEER_VERSION})", node.get("version"))
    node.update(node=p.name, state="ok" if node.get("state", "ok") == "ok" else str(node["state"]))
    return node


def reachable(peer, budget: float = REACH_BUDGET) -> tuple[str | None, str]:
    """(host, via) of the first configured host that answers a ping, within `budget` seconds in all.
    Unlike hosts.resolve no Bonjour or ARP fallback: the monitor must stay bounded."""
    from docsync import discover
    end = time.monotonic() + budget
    for h, via in discover.order_hosts(peer.hosts):
        if h.startswith("/"):
            return h, "local directory"
        if via.endswith("(link down)"):
            continue
        left_ms = int((end - time.monotonic()) * 1000)
        if left_ms < 100:
            break
        if discover.ping(h, timeout_ms=min(800, left_ms)) is not None:
            return h, via
    return None, ""


def _peer(item, ssh_cmd, end: float) -> dict:
    """One peer within the report's deadline: a configured Peer is located first, a PeerSpec as given."""
    from docsync import api
    if not isinstance(item, api.PeerSpec):
        host, via = reachable(item)
        item = api.PeerSpec(item.name, host, item.user, via=via, hosts_tried=list(item.hosts),
                            paths={"documents": item.path})
    return probe_peer(item, ssh_cmd, timeout=max(1, min(PROBE_TIMEOUT, end - time.monotonic())))


def gather(cfg, peers: list | None = None, *, local: bool = False, state_dir=None, log_dir=None, ssh_cmd=None) -> dict:
    """The report: this Mac and every peer (api.PeerSpec list; the config's peers, located here, when
    None) unless `local`, all in parallel and within TOTAL_BUDGET seconds in all."""
    from docsync import __version__
    end = time.monotonic() + TOTAL_BUDGET
    items = [] if local or cfg is None else list(cfg.peers if peers is None else peers)
    late = f"error: no answer within the monitor budget of {TOTAL_BUDGET:g} s"
    ex = ThreadPoolExecutor(max_workers=1 + len(items))
    futs = [ex.submit(monitor.local_node, cfg, state_dir, log_dir)] + [ex.submit(_peer, i, ssh_cmd, end) for i in items]
    nodes = []
    for k, f in enumerate(futs):
        try:
            nodes.append(f.result(timeout=max(0.0, end - time.monotonic()) + 1))
        except FuturesTimeout:
            name = _local_name() if k == 0 else items[k - 1].name
            nodes.append(stub_node(name, late))
    ex.shutdown(wait=False)  # a late worker ends on its own timeouts; nobody waits for it here
    flat = []
    for n in nodes:
        found = alerts(n)
        n["alerts"] = [a.to_dict() for a in found]
        flat += [a.to_dict(n["node"]) for a in found]
    code = 1 if any(a["level"] == "FAIL" for a in flat) else 0
    return {"schema": SCHEMA, "version": __version__, "checked_at": round(time.time(), 3), "ok": code == 0,
            "exit_code": code, "nodes": nodes, "alerts": flat}


def _local_name() -> str:
    from docsync import icloud
    return icloud.node_name()


# ── table ───────────────────────────────────────────────────────────────────

def _agent_cell(a: dict) -> str:
    if not a.get("loaded"):
        return "not loaded"
    bits = [a.get("state") or "?", f"exit {'-' if a.get('last_exit') is None else a['last_exit']}"]
    if a.get("interval_s"):
        bits.append(f"/{fmt_age(a['interval_s'])}")
    bits.append(f"max {a['max_runtime']:g}s" if a.get("max_runtime") is not None else "no max-runtime")
    if not a.get("program_ok"):
        bits.append("FOREIGN")
    return " ".join(bits)


def _short(command: str) -> str:
    """'/long/path/python -m docsync sync --all' -> 'python -m docsync sync --all' (max 40 chars)."""
    head, _, rest = command.partition(" ")
    return f"{head.rsplit('/', 1)[-1]} {rest}".strip()[:40]


def _running_cell(r: dict) -> str:
    live = [h for h in [r["run_lock"]] + r["root_locks"] if h and h["alive"]]
    if not live:
        return "idle"
    h = live[0]
    more = f" +{len(live) - 1}" if len(live) > 1 else ""
    return f"pid {h['pid']} {fmt_age(time.time() - h['started'])} {_short(h['command'])}{more}"


def _run_cell(run: dict | None) -> str:
    if run is None:
        return "none"
    if run["kind"] == "deadline":
        text = f"{run['time'][5:16]} DEADLINE"
    else:
        text = (f"{run['time'][5:16]} {run['ok']}/{run['failed']}/{run['busy']} ok/fail/busy {run['files']} files"
                + (f" {run['conflicts']} confl" if run["conflicts"] else "")
                + (f" lock-lost {run['peer_lock_lost']}" if run["peer_lock_lost"] else "")
                + (f" symlink-skip {run['symlink_skipped']}" if run["symlink_skipped"] else ""))
    o = run["origin"]
    return text + (" [no origin]" if o is None else " [agent]" if o["agent"] else " [NOT agent]")


def _icloud_cell(ic: dict | None) -> str:
    if ic is None:
        return "-"
    bad = [r for r, ok in ic["roots"].items() if not ok]
    return f"{'ok' if not bad else 'NOT ok ' + ','.join(bad)} {fmt_age(ic['age_s'])}"


def row(n: dict) -> list[str]:
    if n["state"] != "ok":
        return [n["node"], n["state"]]
    ok = n["last_success"]
    return [n["node"], _agent_cell(n["agent"]), _running_cell(n["running"]), _run_cell(n["last_run"]),
            "never" if ok is None else fmt_age(ok["age_s"]),
            "?" if n["conflicts"] is None else str(sum(n["conflicts"].values())),
            str(len(n["deadline_dumps"])), _icloud_cell(n["icloud"])]


HEAD = ["node", "agent", "running now", "last run", "last ok", "confl", "dumps", "icloud"]


def render(rep: dict) -> str:
    rows = [HEAD] + [row(n) for n in rep["nodes"]]
    width = [max(len(r[i]) for r in rows if len(r) > i and len(r) == len(HEAD)) for i in range(len(HEAD))]
    out = []
    for r in rows:
        if len(r) != len(HEAD):  # a node without facts: its state across the row
            out.append(f"{r[0]:<{width[0]}}  {r[1]}")
        else:
            out.append("  ".join(c.ljust(w) for c, w in zip(r, width)).rstrip())
    out += [f"{a['level']} {a['node']}: {a['text']}" for a in rep["alerts"]]
    out.append("monitor: FAIL" if rep["exit_code"] else "monitor: ok")
    return "\n".join(out)


# ── command ─────────────────────────────────────────────────────────────────

def cmd_monitor(a) -> int:
    from docsync.config import load_config
    try:
        cfg = load_config()
    except FileNotFoundError:
        cfg = None  # no config: this Mac only, conflicts unknown
    interval = 0 if a.once else a.interval
    try:
        while True:
            rep = gather(cfg, local=a.local)
            text = json.dumps(rep, ensure_ascii=False) if a.json else render(rep)
            if interval and not a.json and sys.stdout.isatty():
                text = "\033[H\033[2J" + time.strftime("%H:%M:%S ") + "docsync monitor\n" + text
            print(text, flush=True)
            if not interval:
                return rep["exit_code"]
            time.sleep(interval)
    except KeyboardInterrupt:
        return 130


def register(sub) -> None:
    s = sub.add_parser("monitor", help="agent, running sync, last run and alerts on this Mac and every peer (read-only)")
    s.add_argument("--once", action="store_true", help="one report, then exit (the default)")
    s.add_argument("--json", action="store_true", help="one JSON object per report instead of the table")
    s.add_argument("--interval", type=float, default=0, metavar="SEC", help="refresh every SEC seconds until Ctrl-C")
    s.add_argument("--local", action="store_true", help="this Mac only (what a peer answers to the monitor)")
    s.set_defaults(fn=cmd_monitor)
