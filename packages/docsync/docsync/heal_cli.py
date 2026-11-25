"""CLI for `docsync heal` and its launchd watcher (`install-heal-agent`, `uninstall-heal-agent`).

Alerts come from docsync.monitor_report.gather() (node dicts) and docsync.monitor_alerts.alerts().
Test hook DOCSYNC_TEST_HEAL_STATUS: a JSON file with node statuses ([{"node", "alerts": [...]}]),
used instead of the monitor."""

from __future__ import annotations

import argparse
import importlib
import json
import os
import sys
import time
from pathlib import Path

from docsync import agent, heal, watchdog
from docsync.config import STATE_DIR, load_config

MONITOR_MISSING = "docsync monitor not available (needs docsync >= 1.3.3)"


def _flatten(nodes: list[dict], alerts_of) -> list[dict]:
    """One dict per alert, with its node name and that node's last run (the stale_success rule needs it)."""
    return [{**a, "node": n.get("node"), "last_run": n.get("last_run")} for n in nodes for a in alerts_of(n)]


def _monitor():
    try:  # import_module honours sys.modules, so a missing or patched monitor is seen
        monitor_alerts = importlib.import_module("docsync.monitor_alerts")
        monitor_report = importlib.import_module("docsync.monitor_report")
        return monitor_alerts.alerts, monitor_report.gather
    except (ImportError, AttributeError):
        return None


def _config():
    try:
        return load_config()
    except FileNotFoundError:
        return None


def collect_alerts(ctx: heal.Context) -> list[dict] | None:
    """Alerts of this node (coordinator: of every node); None when the monitor modules are missing."""
    hook = os.environ.get("DOCSYNC_TEST_HEAL_STATUS")
    if hook:
        return _flatten(json.loads(Path(hook).read_text()), lambda n: n.get("alerts", []))
    mon = _monitor()
    if mon is None:
        return None
    alerts_of, gather = mon
    nodes = gather(_config(), local=not ctx.coordinator)["nodes"]
    return _flatten(nodes, lambda n: [a.to_dict(node=n.get("node")) for a in alerts_of(n)])


def reprobe_for(ctx: heal.Context):
    """reprobe(node) -> that node's alert codes from one fresh monitor probe; None when unknown."""
    def reprobe(node: str) -> list[str] | None:
        if os.environ.get("DOCSYNC_TEST_HEAL_STATUS") or _monitor() is None:
            return None
        alerts_of, gather = _monitor()
        cfg = _config()
        local = node == ctx.node
        peers = [p for p in (cfg.peers if cfg else []) if p.name == node]
        if not local and not peers:
            return None
        rows = [n for n in gather(cfg, peers, local=local)["nodes"] if n.get("node") == node]
        return [a.code for a in alerts_of(rows[0])] if rows else None
    return reprobe


def heal_once(a, budget_end: float | None = None) -> int:
    ctx = heal.default_context(coordinator=a.coordinator)
    if a.coordinator and not ctx.coordinator:
        print("docsync heal: --coordinator needs cluster.toml and maccluster here — healing this node only")
    found = collect_alerts(ctx)
    if found is None:
        print(MONITOR_MISSING)
        return 2
    res = heal.Healer(ctx, notify=not a.no_notify, dry_run=a.dry_run, use_fm=not a.no_fm,
                      reprobe=reprobe_for(ctx), budget_end=budget_end).run(found)
    print("\n".join(res.lines))
    print("heal: ok" if res.exit_code == 0 else "heal: FAIL remains")
    return res.exit_code


def _deadline(seconds: float) -> None:
    """Watchdog callback: leave a trace and end this heal run (exit 4). Children are left alone."""
    try:
        with open(STATE_DIR / "history.log", "a") as fh:
            fh.write(f"\n=== heal {time.strftime('%Y-%m-%d %H:%M:%S')} deadline of {seconds:g} s exceeded\n")
        print(f"docsync heal: deadline of {seconds:g} s exceeded", file=sys.stderr, flush=True)
    finally:
        os._exit(watchdog.EXIT_DEADLINE)


def cmd_heal(a) -> int:
    while True:
        watchdog.arm(a.max_runtime, lambda: _deadline(a.max_runtime))
        try:
            rc = heal_once(a, time.monotonic() + a.max_runtime if a.max_runtime > 0 else None)
        finally:
            watchdog.disarm()
        if a.once or rc == 2:
            return rc
        time.sleep(max(1, a.interval) * 60)


def register(sub) -> None:
    s = sub.add_parser("heal", help="heal what the monitor reports: safe actions automatically, advice for the rest")
    s.add_argument("--once", action="store_true", help="one pass, then exit (default: every --interval minutes)")
    s.add_argument("--dry-run", action="store_true", help="only show what would be done")
    s.add_argument("--no-notify", action="store_true", help="no Notification Center messages")
    s.add_argument("--no-fm", action="store_true", help="rule table only, no Apple Foundation Model")
    s.add_argument("--coordinator", action="store_true",
                   help="also heal the other cluster.toml nodes (only on the one Mac that heals the cluster)")
    s.add_argument("--interval", type=int, default=15, metavar="MIN", help=argparse.SUPPRESS)
    s.add_argument("--max-runtime", type=float, default=agent.HEAL_MAX_RUNTIME, metavar="SEC",
                   help=f"end one pass with exit 4 after SEC seconds (default {agent.HEAL_MAX_RUNTIME}, 0 = no limit)")
    s.set_defaults(fn=cmd_heal)
    s = sub.add_parser("install-heal-agent", help="launchd: docsync heal --once every N minutes")
    s.add_argument("--interval", type=int, default=15, metavar="MIN")
    s.add_argument("--coordinator", action="store_true", help="the agent runs heal --coordinator")
    s.set_defaults(fn=lambda a: print(agent.install_heal(a.interval, a.coordinator)) or 0)
    sub.add_parser("uninstall-heal-agent").set_defaults(fn=lambda a: print(agent.uninstall_heal()) or 0)


def main(argv: list[str]) -> int:
    """`docsync heal ARGV` without the rest of the CLI (tests)."""
    ap = argparse.ArgumentParser(prog="docsync")
    register(ap.add_subparsers(dest="cmd", required=True))
    a = ap.parse_args(["heal", *argv])
    return int(a.fn(a) or 0)
