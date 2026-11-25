"""`docsync heal`: turn monitor alerts into a fixed allowlist of safe actions, each verified.

Rule table (alert code → action) decides; the Apple FM (heal_fm) may only choose among the same
allowed actions or none. Conflicts and held-back deletions stay with the user: heal only advises.
Hard bans: never --force-deletes, never touch conflict copies, never edit config.toml/cluster.toml,
never kill a process (kill -0 only). Every external effect goes through `runner`."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path

from docsync import advisor, agent, agentcheck, heal_fm, lock, macos
from docsync.config import CONFIG_FILE, MACCLUSTER_CONFIG, STATE_DIR
from docsync.heal_rules import ICLOUD_SWITCH, Context, allowed_actions, wake_targets

RATE_LIMIT_S = 30 * 60
GIVE_UP_AFTER = 3
LOCK_WAIT_S = 15
PING_TRIES = 30  # 2 s apart: ~60 s for a woken Mac to answer
SYNC_ARGS = ["sync", "--all", "--max-runtime", "1800", "--no-notify", "--from-heal"]  # origin agent=heal
HEALING = ("reload_agent", "sync_now", "wake_peer", "setup_node", "update_peer")
NOTIFYING = ("notify", "summarize_dump", "advise_conflicts", "advise_deletes")  # once per episode
REMIND_S = 24 * 3600
_DETACHED: list = []  # started syncs, kept referenced so they are never reaped (or warned about) by GC


@dataclass
class HealResult:
    exit_code: int
    lines: list[str]


def _toml(path: Path) -> dict:
    try:
        return tomllib.loads(Path(path).read_text())
    except (OSError, ValueError):
        return {}


def default_context(coordinator: bool = False) -> Context:
    """This Mac. Coordinator only when asked (--coordinator) and cluster.toml + maccluster exist."""
    import shutil
    from docsync import icloud, watchdog
    nodes = {str(n["id"]): dict(n) for n in _toml(MACCLUSTER_CONFIG).get("nodes", []) if n.get("id")}
    macs = {str(p["name"]): str(p["wake_mac"]) for p in _toml(CONFIG_FILE).get("peers", [])
            if p.get("name") and p.get("wake_mac")}
    mc = shutil.which("maccluster")
    return Context(node=icloud.node_name(), coordinator=coordinator and MACCLUSTER_CONFIG.exists() and bool(mc), cluster_nodes=nodes,
                   python=sys.executable, state_dir=STATE_DIR, log_dir=watchdog.log_dir(), maccluster=mc,
                   maccluster_src=Path("~/Developer/maccluster").expanduser(), plist=agent.PLIST, uid=os.getuid(),
                   wake_macs=macs)


# ── real effects (tests inject fakes) ────────────────────────────────────────

def real_runner(argv: list[str], timeout: float = 60, env: dict | None = None, detach: bool = False,
                cwd: str | None = None) -> tuple[int, str]:
    """(rc, output). detach=True starts the command in its own session and returns (0, pid)."""
    if detach:
        p = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             env=env, cwd=cwd, start_new_session=True)
        _DETACHED.append(p)
        return 0, str(p.pid)
    try:
        res = subprocess.run(argv, capture_output=True, text=True, timeout=timeout or None, env=env, cwd=cwd)
    except subprocess.TimeoutExpired:
        return 124, f"timeout after {timeout:g} s"
    except OSError as e:
        return 127, str(e)
    return res.returncode, (res.stdout + res.stderr).strip()


def _alive(pid: int) -> bool:
    """kill -0: does the process exist? Never sends a real signal."""
    try:
        os.kill(pid, 0)
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def magic_packet(mac: str) -> bytes:
    raw = bytes.fromhex(mac.replace(":", "").replace("-", ""))
    if len(raw) != 6:
        raise ValueError(f"not a MAC address: {mac}")
    return b"\xff" * 6 + raw * 16


def send_wol(mac: str) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        s.sendto(magic_packet(mac), ("255.255.255.255", 9))


def _valid_entry(v) -> bool:
    """A heal-state entry: a dict whose last/fails/ok_at/notified, when present, are numbers."""
    def num(x):
        return isinstance(x, (int, float)) and not isinstance(x, bool)
    return isinstance(v, dict) and num(v.get("last")) and num(v.get("fails")) \
        and all(num(v[f]) for f in ("ok_at", "notified") if f in v)


def _tail(text: str, n: int = 160) -> str:
    one = " ".join((text or "").split())
    return one[-n:] if one else "no output"


# ── the healer ──────────────────────────────────────────────────────────────

class Healer:
    def __init__(self, ctx: Context, runner=real_runner, *, notify: bool = True, dry_run: bool = False,
                 use_fm: bool = True, now=time.time, sleep=time.sleep, alive=_alive, send_wol=send_wol,
                 reprobe=None, budget_end: float | None = None, clock=time.monotonic):
        """reprobe(node) -> alert codes of that node now (None = unknown); budget_end: clock() deadline."""
        self.ctx, self.run_cmd, self.notify_on, self.dry, self.use_fm = ctx, runner, notify, dry_run, use_fm
        self.now, self.sleep, self.alive, self.wol = now, sleep, alive, send_wol
        self.reprobe, self.budget_end, self.clock = reprobe, budget_end, clock
        self.lines: list[str] = []
        self.pass_alerts: list[dict] = []

    @property
    def state_file(self) -> Path:
        return self.ctx.state_dir / "heal-state.json"

    def run(self, alerts: list[dict]) -> HealResult:
        self.lines = []
        state = {} if self.dry else self._load_state()
        healed = []
        for a, action, source, reason in self._decide(alerts):
            status, text = self._attempt(state, a, action)
            self.lines.append(f"alert {a.get('node')} {a.get('code')} → {action} ({source}): {text}")
            if reason:
                self.lines.append(f"   reason: {reason}")
            if status == "ok" and action in HEALING:
                healed.append(a)
        if not alerts:
            self.lines.append("no alerts")
        if not self.dry:
            live = {(a.get("node"), a.get("code")) for a in alerts}
            self._save_state({k: v for k, v in state.items() if tuple(k.split("|")[:2]) in live})
            self._append_history()
        left = [a for a in alerts if a.get("level") == "FAIL" and not any(a is h for h in healed)]
        return HealResult(1 if left else 0, self.lines)

    def _decide(self, alerts: list[dict]):
        self.pass_alerts = list(alerts)
        per = [(a, allowed_actions(a, self.ctx, alerts)) for a in alerts]
        union: dict[str, list[str]] = {}
        for a, acts in per:
            for act in acts:
                union.setdefault(a["code"], [])
                if act not in union[a["code"]]:
                    union[a["code"]].append(act)
        picks = None
        if self.use_fm and union:
            picks = heal_fm.choose([a for a, acts in per if acts], {"node": self.ctx.node,
                                   "coordinator": self.ctx.coordinator}, union, log=self.lines.append,
                                   budget=self._budget())
        chosen = {d["code"]: d for d in picks or []}
        for a, acts in per:
            d = chosen.get(a.get("code"))
            if d and (d["action"] == "none" or d["action"] in acts):
                yield a, d["action"], "fm", d["reason"]
            else:
                if d:
                    self.lines.append(f"fm proposed disallowed action {d['action']} for {a.get('node')}")
                yield a, acts[0] if acts else "none", "rule", ""

    def _budget(self) -> float | None:
        """Seconds left in this pass (None = no limit)."""
        return None if self.budget_end is None else self.budget_end - self.clock()

    def _attempt(self, state: dict, a: dict, action: str) -> tuple[str, str]:
        if action == "none":
            return "none", "no safe automatic action"
        node, code = a.get("node"), a.get("code")
        key = f"{node}|{code}|{action}"
        st = state.setdefault(key, {"last": 0, "fails": 0})
        if st.get("gave_up"):
            return "skip", f"skipped: gave up after {st['fails']} failures ({st.get('last_error', '?')})"
        if action in NOTIFYING and st.get("notified") and self.now() - st["notified"] < REMIND_S:
            return "skip", f"skipped: already notified (reminder after {REMIND_S // 3600} h)"
        if action not in NOTIFYING and st["last"] and self.now() - st["last"] < RATE_LIMIT_S:
            return "skip", f"skipped: rate limit (last try {int((self.now() - st['last']) // 60)} min ago)"
        if self.dry:
            return "dry", "would run (dry-run)"
        if st.pop("ok_at", None):  # "ok" last time, yet the alert is still here: that try did not heal
            st["fails"] += 1
            st["last_error"] = f"{code} still present after {action}"
            if st["fails"] >= GIVE_UP_AFTER:
                st["gave_up"] = True
                self._notify("docsync heal", f"heal gave up on {code} at {node}: {st['last_error']}")
                return "skip", f"skipped: gave up after {st['fails']} tries ({st['last_error']})"
        try:
            status, text = getattr(self, "act_" + action)(a)
        except Exception as e:  # one broken action must not stop the others
            status, text = "fail", f"error: {e!r}"
        st["last"] = self.now()
        if status == "fail":
            st["fails"] += 1
            st["last_error"] = text
        elif status == "ok" and action in HEALING:
            st["ok_at"] = self.now()
        elif status == "ok":
            st["notified"] = self.now()
        if action in HEALING and status in ("ok", "fail"):
            self._notify(f"docsync heal: {action}", f"{node} {code}: {text}")
        if status == "fail" and st["fails"] >= GIVE_UP_AFTER:
            st["gave_up"] = True
            self._notify("docsync heal", f"heal gave up on {code} at {node}: {text}")
        return status, {"ok": "ok", "fail": "failed", "skip": "skipped"}[status] + f": {text}"

    def _notify(self, title: str, text: str, subtitle: str = "") -> bool:
        if not self.notify_on:
            return False
        self.run_cmd(macos.notify_argv(title, text, subtitle), timeout=10)
        return True

    # ── actions: each does one thing, then verifies it ──────────────────────

    def act_reload_agent(self, a: dict) -> tuple[str, str]:
        if held := agent.hold_reason():  # an incident pause (bootout + hold file) is never undone here
            return "skip", held
        c = self.ctx
        if c.plist.exists():
            how, (rc, out) = "bootstrap", self.run_cmd(["launchctl", "bootstrap", f"gui/{c.uid}", str(c.plist)], timeout=30)
        else:
            how, (rc, out) = "install-agent", self.run_cmd([c.python, "-m", "docsync", "install-agent", "--interval", "15"],
                                                           timeout=120)
        ok, why = agentcheck.check(*self.run_cmd(["launchctl", "print", f"gui/{c.uid}/{agent.LABEL}"], timeout=10),
                                   run=self.run_cmd)
        return ("ok", f"{how}; {why}") if ok else ("fail", f"{why} after {how} (exit {rc}: {_tail(out)})")

    def act_sync_now(self, a: dict) -> tuple[str, str]:
        h = lock.holder(self.ctx.state_dir)
        if h and self.alive(h["pid"]):
            return "skip", f"run in progress (pid {h['pid']})"
        rc, out = self.run_cmd([self.ctx.python, "-m", "docsync", *SYNC_ARGS], timeout=0, detach=True)
        if rc != 0 or not out.isdigit():
            return "fail", f"could not start sync: {_tail(out)}"
        pid = int(out)
        for _ in range(LOCK_WAIT_S):
            h = lock.holder(self.ctx.state_dir)
            if h and h["pid"] == pid:
                return "ok", f"sync started (pid {pid}) and holds the run lock"
            self.sleep(1)
        return "fail", f"sync (pid {pid}) did not take the run lock within {LOCK_WAIT_S} s"

    def act_wake_peer(self, a: dict) -> tuple[str, str]:
        """peer_unreachable: wake the alert's node; peer_failed / stale_success: every peer that did not answer."""
        targets = (wake_targets(a, self.pass_alerts)
                   if a.get("code") in ("peer_failed", "stale_success") else [a["node"]])
        results = [self._wake(n) for n in targets]
        ok = bool(results) and all(r[0] == "ok" for r in results)
        return ("ok" if ok else "fail"), "; ".join(r[1] for r in results) or "no unreachable peer"

    def _wake(self, node: str) -> tuple[str, str]:
        info = self.ctx.cluster_nodes.get(node, {})
        ip, mac = info.get("ip"), info.get("wake_mac") or self.ctx.wake_macs.get(node)
        if not ip:
            return "fail", f"no ip for {node} in cluster.toml"
        if mac:
            self.wol(str(mac))
        for _ in range(PING_TRIES if mac else 1):
            if self.run_cmd(["/sbin/ping", "-c", "1", "-t", "2", str(ip)], timeout=5)[0] == 0:
                return "ok", f"{node} {ip} answers ping" + (" after Wake-on-LAN" if mac else "")
            self.sleep(2)
        return "fail", f"{node} {ip} does not answer ping" + (" after Wake-on-LAN" if mac else " (no wake_mac configured)")

    def act_setup_node(self, a: dict) -> tuple[str, str]:
        if not self.ctx.maccluster:
            return "fail", "maccluster not found"
        rc, out = self.run_cmd([self.ctx.maccluster, "sync", "setup", "--node", a["node"]], timeout=600)
        if rc != 0:
            return "fail", f"{_tail(out)} (exit {rc})"
        return self._verify_gone(a, f"maccluster sync setup: {_tail(out, 80)}")

    def _verify_gone(self, a: dict, done: str) -> tuple[str, str]:
        """After setup/update: one fresh monitor probe of the node must no longer report the alert."""
        codes = self.reprobe(a["node"]) if self.reprobe else None
        if codes is not None and a.get("code") in codes:
            return "fail", f"{a['code']} still reported by {a['node']} after: {done}"
        return "ok", done + ("" if codes is None else "; verified by a fresh monitor probe")

    def act_update_peer(self, a: dict) -> tuple[str, str]:
        src, node = self.ctx.maccluster_src, a["node"]
        if not src.is_dir():  # a failure, so the give-up after 3 ends the notifications
            return "fail", f"maccluster source {src} missing"
        info = self.ctx.cluster_nodes.get(node, {})
        ip, user = info.get("ip"), str(info.get("ssh_target", "")).rpartition("@")[0]
        if not ip or not user or not self.ctx.maccluster:
            return "fail", (f"no ip for {node} in cluster.toml" if not ip else
                            f"no user in ssh_target of {node} in cluster.toml" if not user else "maccluster not found")
        # --no-config: else this Mac's cluster.toml is copied to the peer; --no-ssh-config: else this Mac's
        # ~/.ssh/config.d/maccluster becomes `Host 10.42.0.* User <user>` and breaks ssh to the other nodes
        rc, out = self.run_cmd([self.ctx.maccluster, "remote-install", str(ip), "--no-config", "--no-ssh-config",
                                "--user", user],
                               timeout=900, env={**os.environ, "MACCLUSTER_SRC": str(src)})
        if rc != 0:
            return "fail", f"{_tail(out)} (remote-install exit {rc})"
        return self.act_setup_node(a)

    def act_summarize_dump(self, a: dict) -> tuple[str, str]:
        dumps = self._dumps(a)
        if not dumps:
            return "skip", "no deadline dump"
        summary = self._dump_summary(dumps[0])
        self._notify("docsync: deadline dump", f"{a['node']}: {summary}", subtitle=dumps[0].name)
        return "ok", f"{dumps[0].name}: {summary}"

    def _dumps(self, a: dict) -> list[Path]:
        """deadline-*.txt dumps, newest first: the alert's paths, but only those inside log_dir."""
        root = self.ctx.log_dir.resolve()
        given = [Path(p).resolve() for p in (a.get("data") or {}).get("paths") or []]
        found = [p for p in given if p.is_file() and p.is_relative_to(root)] or list(root.glob("deadline-*.txt"))
        return sorted(found, key=lambda p: p.stat().st_mtime, reverse=True)

    def _dump_summary(self, dump: Path) -> str:
        text = dump.read_text(errors="replace")[-8000:]
        summary = heal_fm.summarize(text, "Wo hing der docsync-Lauf, der seine Deadline überschritt? Thread-Stacks.",
                                    budget=self._budget()) if self.use_fm else None
        if summary:
            return summary
        frames = [ln.strip() for ln in text.splitlines() if ln.strip().startswith("File ")]
        return f"zuletzt {frames[-1]}" if frames else _tail(text, 120)

    def act_advise_conflicts(self, a: dict) -> tuple[str, str]:
        rc, out = self.run_cmd([self.ctx.python, "-m", "docsync", "advise"], timeout=120)
        advice = [ln.strip() for ln in out.splitlines() if ln.strip() and not ln.startswith("──")]
        if rc not in (0, 1) or not advice:
            return "fail", f"docsync advise exit {rc}: {_tail(out)}"
        self._notify("docsync: conflicts", " ".join(advice), subtitle=f"{a['node']}: review the conflict copies")
        return "ok", "advice: " + " | ".join(advice)

    def act_advise_deletes(self, a: dict) -> tuple[str, str]:
        hist = self.ctx.state_dir / "history.log"
        block = advisor.last_run_block(hist.read_text(errors="replace")) if hist.exists() else ""
        facts = [ln.strip() for ln in block.splitlines() if "deletion guard" in ln or ln.startswith("── ")]
        guards = [f for f in facts if "deletion guard" in f]
        if not guards:
            return "skip", "no held-back deletions in the last report"
        assessment = heal_fm.summarize("\n".join(facts), "Bewertung zurückgehaltener Löschungen: sieht es nach "
                                       "Umbenennung oder Groß-/Kleinschreibung aus oder nach echter Löschung?",
                                       budget=self._budget()) if self.use_fm else None
        assessment = assessment or f"{len(guards)} Lauf/Läufe mit zurückgehaltenen Löschungen: {guards[0][:120]}"
        self._notify("docsync: held-back deletions", assessment, subtitle="review, then docsync sync --force-deletes")
        return "ok", f"{assessment} — review, then docsync sync --force-deletes"

    def act_notify(self, a: dict) -> tuple[str, str]:
        node, code, data = a.get("node"), a.get("code"), a.get("data") or {}
        if code == "icloud_incomplete":
            roots = ", ".join(data.get("roots") or []) or "?"
            text = f"{node}: turn on {ICLOUD_SWITCH} (roots: {roots})"
        elif code == "agent_exit":
            dumps = self._dumps({})
            text = (f"{node}: the sync agent's last run exited {data.get('exit', '?')}"
                    + (f"; {dumps[0].name}: {self._dump_summary(dumps[0])}" if dumps else ""))
        elif code == "stale_success":
            text = f"{node}: no successful sync for {data.get('age_min', '?')} min — no peer was reachable or free"
        elif code == "peer_failed":
            text = f"{node}: last run failed for " + ("; ".join(data.get("reasons") or []) or a.get("text") or "?")
        elif code == "non_agent_run":
            text = f"{node}: a sync ran outside the agent ({data.get('origin', '?')})"
        else:
            text = f"{node}: {a.get('text') or code}"
        sent = self._notify(f"docsync: {code}", text)
        return "ok", text if sent else f"{text} (not sent: --no-notify)"

    # ── state and history ───────────────────────────────────────────────────

    def _load_state(self) -> dict:
        try:
            data = json.loads(self.state_file.read_text())
        except (OSError, ValueError):
            return {}
        if not isinstance(data, dict):
            return {}
        return {k: v for k, v in data.items() if isinstance(k, str) and _valid_entry(v)}

    def _save_state(self, state: dict) -> None:
        try:
            self.ctx.state_dir.mkdir(parents=True, exist_ok=True)
            tmp = self.state_file.with_suffix(".tmp")
            tmp.write_text(json.dumps(state, indent=1, sort_keys=True))
            os.replace(tmp, self.state_file)
        except OSError:
            pass

    def _append_history(self) -> None:
        stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.now()))
        try:
            with open(self.ctx.state_dir / "history.log", "a") as fh:
                fh.write(f"\n=== heal {stamp}\n" + "\n".join(self.lines) + "\n")
        except OSError:
            pass
