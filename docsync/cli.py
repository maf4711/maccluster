"""docsync command line."""

from __future__ import annotations

import argparse
import json
import math
import os
import signal
import sys
import time
from pathlib import Path

from docsync import __version__, advisor, agent, api, cli_tools, icloud, lock, macos, thunderbolt, watchdog
from docsync.config import (CONFIG_FILE, DEFAULT_EXCLUDES, MACCLUSTER_CONFIG, STATE_DIR, Config, Peer, add_llm_roots,
                            load_config, local_hostnames, peers_from_maccluster, save_config)
from docsync.hosts import resolve, resolve_peer, select_peers
from docsync.manifest import scan
from docsync.report import RunReport, fmt_bytes
from docsync.session import base_file


def _lock(state_dir: Path, wait: float = 0.0):
    """Exclusive run lock. With wait > 0, poll for up to `wait` seconds before giving up (exit 3)."""
    fh = lock.acquire(state_dir, wait)
    if fh is None:
        print(f"docsync: {lock.busy_message(state_dir)} — exiting", file=sys.stderr)
        sys.exit(3)
    return fh


# ── commands ────────────────────────────────────────────────────────────────

def cmd_init(a) -> int:
    if CONFIG_FILE.exists() and not a.force:
        print(f"config exists: {CONFIG_FILE} (use --force to overwrite)")
        return 1
    cfg = Config(path=a.path)
    if a.from_maccluster:
        if not MACCLUSTER_CONFIG.exists():
            sys.exit(f"no maccluster config at {MACCLUSTER_CONFIG}")
        cfg.peers = peers_from_maccluster(MACCLUSTER_CONFIG.read_text(), local_hostnames(), user=a.user)
    for spec in a.peer or []:
        name, _, hosts = spec.partition("=")
        cfg.peers.append(Peer(name=name, hosts=hosts.split(",") if hosts else [name + ".local"], user=a.user))
    save_config(cfg)
    print(f"wrote {CONFIG_FILE}")
    for p in cfg.peers:
        print(f"   peer {p.name:<10} {p.userhost(p.hosts[0])}:{p.path}  (+{len(p.hosts) - 1} fallback hosts)")
    if not cfg.peers:
        print("   no peers yet — add [[peers]] blocks or re-run with --peer NAME=host1,host2")
    return 0


def cmd_roots_add_llm(a) -> int:
    try:
        added = add_llm_roots(CONFIG_FILE)
    except ValueError as e:
        print(f"docsync: {e}", file=sys.stderr)
        return 1
    print(f"added roots {', '.join(added)} to {CONFIG_FILE}" if added else "llm roots already present")
    return 0


def watch_paths(cfg: Config) -> list[str]:
    """launchd WatchPaths: roots with includes (~/.claude, ~/.codex change all the time) sync on the interval only."""
    return [str(cfg.for_root(r).local_root) for r in cfg.effective_roots() if r.includes is None]


def cmd_peers(a) -> int:
    cfg = load_config()
    for r in cfg.effective_roots():
        print(f"root {r.name:<10} {r.path}  (peer side: {r.peer_path or ('per-peer path' if r.legacy else r.path)})")
    print(f"policy: {cfg.conflict_policy}   max_delete: {cfg.max_delete}   safetynet: {'on' if cfg.safetynet else 'off'}")
    print(thunderbolt.render(thunderbolt.state()))
    for p in cfg.peers:
        host, via = resolve_peer(p)
        state = f"→ {host} via {via}" if host else "unreachable"
        cands = [base_file(p.name, STATE_DIR, r.name) for r in cfg.effective_roots()] + [STATE_DIR / f"base-{p.name}.json"]
        mt = max((b.stat().st_mtime for b in cands if b.exists()), default=None)
        last = time.strftime("%Y-%m-%d %H:%M", time.localtime(mt)) if mt else "never"
        print(f"   {p.name:<10} user={p.user or os.environ.get('USER', '?'):<8} {state:<48} last sync: {last}")
    print(agent.status())
    return 0


def cmd_status(a) -> int:
    cfg = load_config()
    m = scan(cfg.local_root, cfg.excludes)
    print(f"{cfg.local_root}: {len(m):,} files, {fmt_bytes(sum(v[1] for v in m.values()))}")
    return cmd_peers(a)


def _icloud_local_roots(specs: list[str] | None) -> list[tuple[str, Path, list[str]]]:
    """--root NAME=PATH (what a coordinator sends), --root NAME (from the config), else the config's
    roots or, on a peer without a config, ~/Documents and ~/Desktop."""
    try:
        cfg = load_config()
    except FileNotFoundError:
        cfg = None
    excludes = list(cfg.excludes if cfg else DEFAULT_EXCLUDES)
    if not specs:
        if cfg is None:
            return [(n, Path(p).expanduser(), excludes) for n, p in icloud.DEFAULT_ROOTS]
        return [(r.name, cfg.for_root(r).local_root, cfg.for_root(r).excludes) for r in cfg.effective_roots()]
    out = []
    for spec in specs:
        name, eq, path = spec.partition("=")
        if not eq and cfg is not None:
            r = cfg.root(name)
            out.append((r.name, cfg.for_root(r).local_root, cfg.for_root(r).excludes))
        else:
            out.append((name, Path(path or dict(icloud.DEFAULT_ROOTS).get(name, f"~/{name}")).expanduser(), excludes))
    return out


def cmd_icloud_check(a) -> int:
    if a.local:
        rep = icloud.local_report(_icloud_local_roots(a.root))
    else:
        cfg = load_config()
        rep = icloud.check(cfg, roots=[cfg.root(n) for n in a.root] if a.root else None,
                           log=(lambda *x: None) if a.json else print)
    print(json.dumps(rep.to_dict(), ensure_ascii=False) if a.json else rep.render())
    return rep.exit_code


def _roots(cfg: Config, names: list[str] | None):
    return [cfg.root(n) for n in names] if names else cfg.effective_roots()


def cmd_sync(a, dry: bool) -> int:
    cfg = load_config()
    if getattr(a, "from_agent", False) and cfg.skip_on_battery and macos.on_battery():
        print("docsync: on battery power and skip_on_battery is set — not syncing")
        return 0
    peers = select_peers(cfg, a.peers, a.all)
    roots = _roots(cfg, getattr(a, "root", None))
    log = (lambda *x: None) if a.quiet else print
    fh = _lock(STATE_DIR, wait=getattr(a, "wait", 0) or 0)
    tb_speed = thunderbolt.state().best_speed if peers else 0
    try:
        specs = []
        for p in peers:  # resolve hosts once per run (quick, sequential, readable output)
            log(f"── {p.name}")
            host, via = resolve(p, log)
            if host and via == "Thunderbolt" and tb_speed:
                via = f"Thunderbolt {tb_speed} Gb/s"
            specs.append(api.PeerSpec(p.name, host, p.user, via=via, hosts_tried=list(p.hosts),
                                      paths={r.name: cfg.peer_path(p, r) for r in roots}))
        if not a.sequential and len(peers) > 1:
            log(f"syncing {len(peers)} peers in parallel, {len(roots)} root{'s' if len(roots) != 1 else ''}")
        result = api.sync(api.SyncRequest(
            roots=[api._ConfigRootSpec(r.name, r.path, r.peer_path, cfg.for_root(r).excludes, r.includes)
                   for r in roots], peers=specs,
            conflict_policy=cfg.conflict_policy, max_delete=cfg.max_delete, force_deletes=a.force_deletes,
            transfer_streams=cfg.transfer_streams, case_insensitive=cfg.case_insensitive, trash=cfg.trash,
            excludes=cfg.excludes, safetynet=cfg.safetynet, safetynet_keep=cfg.safetynet_keep,
            safetynet_max_age_days=cfg.safetynet_max_age_days, safetynet_max_gb=cfg.safetynet_max_gb,
            dry_run=dry, parallel=not a.sequential, max_workers=a.jobs, verbose=a.verbose,
            terminal_progress=not a.quiet and sys.stderr.isatty(), log=log, state_dir=STATE_DIR,
            lock=False))  # the CLI already holds the lock (it supports --wait and prints the exit-3 message)
        run = result.report
    finally:
        fh.close()
    print()
    print(run.render())
    if getattr(a, "json", False):
        print(json.dumps(result.to_dict(), ensure_ascii=False))
    _append_log(run)
    if not dry and not getattr(a, "no_notify", False):
        note = run.notable()
        if note:
            macos.notify("docsync", note, subtitle="sync needs a look — docsync history")
    return result.exit_code


MAX_RUNTIME_LIMIT = 10_000_000  # seconds (~115 days)


def _max_runtime(text: str) -> float:
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"invalid number: {text!r}") from None
    if value < 0:
        raise argparse.ArgumentTypeError("--max-runtime must be >= 0")
    if not math.isfinite(value) or value > MAX_RUNTIME_LIMIT:
        # inf/nan/1e20 would overflow the watchdog's wait and silently leave the run unbounded
        raise argparse.ArgumentTypeError(f"--max-runtime must be a finite number between 0 and {MAX_RUNTIME_LIMIT}")
    return value


def _deadline_exceeded(seconds: float) -> None:
    """Watchdog callback (runs in its thread): leave a trace, kill our rsync/ssh children, exit 4.
    os._exit skips cleanup on purpose — a hung thread must not keep the process (and the lock) alive."""
    msg = f"deadline of {seconds:g} s exceeded"
    try:  # where it hung: every thread's stack, before the children (rsync, ssh) are killed
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        dump = watchdog.dump_stacks(watchdog.log_dir() / f"deadline-{stamp}.txt")
        _append_text(f"{msg} — thread stacks in {dump}")
    except Exception:  # noqa: BLE001 — the exit below matters more than the dump
        _append_text(msg)
    try:
        print(f"docsync: {msg} — ending this run (exit {watchdog.EXIT_DEADLINE})", file=sys.stderr, flush=True)
        sys.stdout.flush()
    except Exception:  # noqa: BLE001 — never let a broken pipe stop the exit
        pass
    watchdog.kill_descendants()
    os._exit(watchdog.EXIT_DEADLINE)


def _with_deadline(a, fn) -> int:
    seconds = getattr(a, "max_runtime", watchdog.DEFAULT_MAX_RUNTIME)
    watchdog.arm(seconds, lambda: _deadline_exceeded(seconds))
    try:
        return fn()
    finally:
        watchdog.disarm()


def _append_log(run: RunReport) -> None:
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        with open(STATE_DIR / "history.log", "a") as fh:
            fh.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')}\n{run.render()}\n")
    except OSError:
        pass


def cmd_conflicts(a) -> int:
    cfg = load_config()
    roots = cfg.effective_roots()
    hits = sorted((f"{r.name}: " if len(roots) > 1 else "") + p
                  for r in roots for p in scan(cfg.for_root(r).local_root, [".docsync*"]) if ".conflict-" in p)
    if not hits:
        print("no conflict copies")
        return 0
    for h in hits:
        print(f"   {h}")
    print(f"{len(hits)} conflict copies — review, then delete the loser; the deletion propagates on the next sync")
    return 0


def cmd_advise(a) -> int:
    if a.check:
        ok, text = advisor.availability()
        print(f"Apple Foundation Model: {text}")
        return 0 if ok else 1
    cfg = load_config()
    hist = STATE_DIR / "history.log"
    report = advisor.last_run_block(hist.read_text()) if hist.exists() else ""
    if a.peers:
        blocks = [b for b in report.split("── ") if b.startswith(tuple(f"{p} " for p in a.peers)) or b.startswith("══") or b.startswith("===")]
        report = "── ".join(blocks) if blocks else report
    tree = scan(cfg.local_root, [], set())
    conflicts = sorted(p for p in tree if ".conflict-" in p)
    sizes = {p: v[1] for p, v in tree.items()}
    warnings = [ln.strip() for ln in report.splitlines() if "⚠" in ln or "FAILED" in ln]
    cloud: set[str] = set()
    n_local = len(scan(cfg.local_root, cfg.excludes, cloud))
    prompt = advisor.build_prompt(report, conflicts, warnings, (len(cloud), n_local), a.peers[0] if a.peers else None, tree)
    if a.show_prompt:
        print(prompt)
        print()
    ok, why = advisor.availability()
    if ok:
        rc, text = advisor.ask(prompt)
        if rc == 0:
            print("── advisor (on-device Apple Foundation Model)")
            print(text)
            return 0
        why = text or f"helper exit {rc}"
    print("── advisor (rule-based fallback — model not used: " + why + ")")
    print("\n".join(advisor.rule_based(conflicts, warnings, sizes)))
    return 1


def cmd_history(a) -> int:
    f = STATE_DIR / "history.log"
    if not f.exists():
        print("no history yet")
        return 0
    lines = f.read_text().splitlines()
    print("\n".join(lines[-a.lines:]))
    return 0


def _watch_run(a) -> int:
    """One sync inside its own forked process, so its deadline (exit 4) ends that run, not the loop."""
    sys.stdout.flush()
    sys.stderr.flush()
    pid = os.fork()
    if pid == 0:
        rc = 1
        try:
            rc = _with_deadline(a, lambda: cmd_sync(a, dry=False))
        except SystemExit as e:
            rc = e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
        except KeyboardInterrupt:
            rc = 130  # Ctrl-C: end quietly, not an error worth a history entry
        except BaseException as e:  # noqa: BLE001 — keep watching, leave a trace
            _append_text(f"watch: unexpected error: {e!r}")
        finally:
            try:
                sys.stdout.flush()
                sys.stderr.flush()
            finally:
                os._exit(rc)
    try:
        return os.waitstatus_to_exitcode(os.waitpid(pid, 0)[1])
    except KeyboardInterrupt:
        # Ctrl-C reaches the whole process group; also forward it in case only we got it, then reap
        try:
            os.kill(pid, signal.SIGINT)
            os.waitpid(pid, 0)
        except (OSError, KeyboardInterrupt):
            pass
        raise


def cmd_watch(a) -> int:
    """Foreground loop with exponential backoff after failures (interval → 2× → … → 6× cap)."""
    base = max(1, int(a.interval * 60))
    delay = base
    while True:
        rc = _watch_run(a)
        if rc == 130:
            raise KeyboardInterrupt  # the run was interrupted: stop watching (main() exits 130)
        delay = base if rc == 0 else min(delay * 2, base * 6)
        if rc != 0:
            print(f"watch: last run rc={rc}, next try in {delay // 60} min", file=sys.stderr)
        time.sleep(delay)


def _append_text(text: str) -> None:
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        with open(STATE_DIR / "history.log", "a") as fh:
            fh.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} {text}\n")
    except OSError:
        pass


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="docsync", description="Sync folders (default ~/Documents) between Macs over the LAN (rsync+ssh, bidirectional).")
    ap.add_argument("--version", action="version", version=f"docsync {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init", help="write the config (optionally import peers from maccluster)")
    s.add_argument("--from-maccluster", action="store_true", help=f"import nodes from {MACCLUSTER_CONFIG}")
    s.add_argument("--peer", action="append", metavar="NAME=host1,host2", help="add a peer (repeatable)")
    s.add_argument("--user", default="", help="ssh user on the peers (default: same as here)")
    s.add_argument("--path", default="~/Documents")
    s.add_argument("--force", action="store_true")
    s.set_defaults(fn=cmd_init)

    s = sub.add_parser("roots", help="manage synced roots in the config")
    rs = s.add_subparsers(dest="roots_cmd", required=True)
    rs.add_parser("add-llm", help="append the llm-claude/llm-codex/llm-gemini allowlist roots to the config"
                  ).set_defaults(fn=cmd_roots_add_llm)
    sub.add_parser("peers", help="peers, reachability, last sync").set_defaults(fn=cmd_peers)
    sub.add_parser("status", help="local tree size + peers").set_defaults(fn=cmd_status)
    s = sub.add_parser("icloud-check", help="iCloud Desktop & Documents: on, uploaded and identical on every node?")
    s.add_argument("--root", action="append", metavar="NAME", help="only this root (repeatable; NAME=PATH with --local)")
    s.add_argument("--json", action="store_true", help="one JSON object instead of the table")
    s.add_argument("--local", action="store_true", help="this Mac only (what a peer answers to the check)")
    s.set_defaults(fn=cmd_icloud_check)
    cli_tools.register(sub)

    for name, dry, help_ in (("sync", False, "sync with peers (bidirectional) and print the report"),
                             ("plan", True, "dry run: show what sync would do")):
        s = sub.add_parser(name, help=help_)
        s.add_argument("peers", nargs="*", help="peer names (default: all)")
        s.add_argument("--all", action="store_true")
        s.add_argument("-v", "--verbose", action="store_true", help="list every file")
        s.add_argument("-q", "--quiet", action="store_true", help="only the final report")
        s.add_argument("--force-deletes", action="store_true", help="override the max_delete guard")
        s.add_argument("--sequential", action="store_true", help="one peer after another (default: all peers in parallel)")
        s.add_argument("--wait", type=float, default=0, metavar="SEC", help="wait up to SEC seconds for a running sync to finish")
        s.add_argument("--max-runtime", type=_max_runtime, default=watchdog.DEFAULT_MAX_RUNTIME, metavar="SEC",
                       help=f"end the whole run with exit 4 after SEC seconds (default {watchdog.DEFAULT_MAX_RUNTIME}, 0 = no limit)")
        s.add_argument("--no-notify", action="store_true", help="no Notification Center message on failures/conflicts")
        s.add_argument("--from-agent", action="store_true", help=argparse.SUPPRESS)
        s.add_argument("-j", "--jobs", type=int, default=0, help="max parallel peers (default: all)")
        s.add_argument("--root", action="append", metavar="NAME", help="only this root (repeatable; default: all roots)")
        s.add_argument("--json", action="store_true", help="also print the result as one JSON object (last line)")
        s.set_defaults(fn=lambda a, d=dry: _with_deadline(a, lambda: cmd_sync(a, dry=d)))

    s = sub.add_parser("watch", help="sync all peers every N minutes (foreground)")
    s.add_argument("--interval", type=int, default=10, metavar="MIN")
    s.add_argument("--max-runtime", type=_max_runtime, default=watchdog.DEFAULT_MAX_RUNTIME, metavar="SEC",
                   help="deadline per run (exit 4 for that run; the loop keeps going)")
    s.set_defaults(fn=cmd_watch, peers=[], all=True, verbose=False, quiet=False, force_deletes=False,
                   sequential=False, jobs=0, wait=0, no_notify=False, from_agent=False, root=None, json=False)

    s = sub.add_parser("advise", help="on-device Apple Foundation Model: what to do about conflicts, held-back deletes, peers")
    s.add_argument("peers", nargs="*", help="limit the report to these peers")
    s.add_argument("--check", action="store_true", help="only print the model availability")
    s.add_argument("--show-prompt", action="store_true")
    s.set_defaults(fn=cmd_advise)
    sub.add_parser("conflicts", help="list *.conflict-* copies").set_defaults(fn=cmd_conflicts)
    s = sub.add_parser("history", help="past reports")
    s.add_argument("-n", "--lines", type=int, default=40)
    s.set_defaults(fn=cmd_history)

    s = sub.add_parser("install-agent", help="launchd: sync --all every N minutes and when ~/Documents changes")
    s.add_argument("--interval", type=int, default=15, metavar="MIN")
    s.add_argument("--no-watch", action="store_true", help="interval only, no WatchPaths trigger")
    s.set_defaults(fn=lambda a: print(agent.install(a.interval, None if a.no_watch else watch_paths(load_config()))) or 0)
    sub.add_parser("uninstall-agent").set_defaults(fn=lambda a: print(agent.uninstall()) or 0)
    return ap


def main(argv: list[str] | None = None) -> int:
    a = build_parser().parse_args(argv)
    try:
        return int(a.fn(a) or 0)
    except FileNotFoundError as e:
        print(f"docsync: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
