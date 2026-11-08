"""`docsync icloud-check`: is iCloud "Desktop & Documents" on, uploaded and identical on every node?

Per node and root: `dd` (File Provider domain attribute on the folder), files not uploaded after 15 min
(Swift helper, URL resource values), stuck `brctl status` items; across nodes, for roots managed
everywhere: same paths (cloud-only counts), same size/mtime (2 s), no iCloud duplicates. Peers answer
for themselves (`icloud-check --local --json` over ssh). Metadata only: no contents, no downloads, no
iCloud setting changed. Results go to `<state dir>/icloud-<node>.json`; `covered()` lets `api.sync`
count an unreachable peer as covered when the last check (<= 24 h) passed the root on both sides."""

from __future__ import annotations

import ctypes
import ctypes.util
import json
import os
import re
import shlex
import shutil
import socket
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from docsync import probe
from docsync.config import MACCLUSTER_CONFIG, STATE_DIR, Config, Peer, Root
from docsync.icloud_parity import parity
from docsync.manifest import excluded, norm, scan
from docsync.report import PeerReport
from docsync.transport import TransportError

DOMAIN_XATTR = "com.apple.file-provider-domain-id"
PENDING_GRACE = 15 * 60       # a file changed this recently may still be on its way up
STATE_MAX_AGE = 24 * 3600
SHOW_PROBLEMS = 20
MIN_PEER_VERSION = "1.3.0"
COVERED = "unreachable — covered by iCloud"
DEFAULT_ROOTS = (("documents", "~/Documents"), ("desktop", "~/Desktop"))
ENABLE_HINT = "(System Settings › Apple ID › iCloud Drive)"
SWIFT_SRC = Path(__file__).resolve().parent / "resources" / "docsync-icloud.swift"
HELPER = STATE_DIR / "bin" / "docsync-icloud"

# `brctl status` is undocumented; same heuristic as the icloud-monitor skill (`icloud.py doctor`):
# "Under /<folder>" sections, items starting at "r:<n> i:<", a part containing "failed" is stuck. Here
# split by part: `> upload…{` (data not in iCloud yet: fails the check once older than 15 min) and
# `> download…{` (an evicted file that could not be fetched: reported, no data at risk).
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
HEADER_RE = re.compile(r"\d+ containers? matching")
UNDER_RE = re.compile(r"^\s*Under (/\S.*)$", re.MULTILINE)
ITEM_START_RE = re.compile(r"\n\s*r:\d+ i:<")
PART_RE = re.compile(r"\n\s*>\s*")
AGE_RE = re.compile(r"last:([0-9.]+)(ms|s|m|h|d) ago")
AGE_UNIT = {"ms": 0.001, "s": 1, "m": 60, "h": 3600, "d": 86400}

# ── facts about this Mac ────────────────────────────────────────────────────

def _hook() -> dict | None:
    """Test hook DOCSYNC_TEST_ICLOUD: a JSON file with this node's iCloud state."""
    f = os.environ.get("DOCSYNC_TEST_ICLOUD")
    if not f:
        return None
    try:
        return json.loads(Path(f).read_text())
    except (OSError, ValueError):
        return {}


def node_name() -> str:
    """This Mac's node name: the maccluster node id whose hostnames are ours, else the short hostname."""
    hook = _hook()
    if hook and hook.get("node"):
        return str(hook["node"])
    try:
        import tomllib
        from docsync.config import _is_self, local_hostnames
        mine = local_hostnames()
        for node in tomllib.loads(MACCLUSTER_CONFIG.read_text()).get("nodes", []):
            if node.get("id") and _is_self([str(h) for h in node.get("hostnames", [])], mine):
                return str(node["id"])
    except (OSError, ValueError):
        pass
    return socket.gethostname().split(".")[0]


def dd_on(name: str, path: Path) -> bool:
    """True when iCloud manages the folder (the attribute is set on ~/Documents and ~/Desktop)."""
    hook = _hook()
    if hook is not None:
        return bool(hook.get("dd", {}).get(name))
    libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
    getxattr = libc.getxattr
    getxattr.restype = ctypes.c_ssize_t
    getxattr.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_uint32, ctypes.c_int]
    return getxattr(os.fsencode(path), DOMAIN_XATTR.encode(), None, 0, 0, 0) >= 0


def _helper() -> Path | None:
    """The Swift helper, compiled on first use (like the advisor's). None without swiftc."""
    if HELPER.exists() and HELPER.stat().st_mtime >= SWIFT_SRC.stat().st_mtime:
        return HELPER
    swiftc = shutil.which("swiftc")
    if not swiftc:
        return None
    HELPER.parent.mkdir(parents=True, exist_ok=True)
    res = subprocess.run([swiftc, "-O", "-o", str(HELPER), str(SWIFT_SRC)], text=True, capture_output=True, timeout=600)
    return HELPER if res.returncode == 0 else None


def _pending_items(name: str, root: Path, excludes: list[str]) -> list[tuple[float, str]] | None:
    hook = _hook()
    if hook is not None:
        items = []
        for rel in hook.get("pending", {}).get(name, []):
            try:
                items.append((os.lstat(root / rel).st_mtime, rel))
            except OSError:
                pass
        return items
    helper = _helper()
    if helper is None:
        return None
    try:
        names = [p for p in excludes if "/" not in p]  # the helper skips these subtrees; '/'-patterns: below
        res = subprocess.run([str(helper), str(root), *names], capture_output=True, timeout=600)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if res.returncode != 0:
        return None
    items = []
    for rec in res.stdout.decode("utf-8", "surrogateescape").split("\0"):
        mtime, _, rel = rec.partition("\t")
        if rel:
            items.append((float(mtime or 0), rel))
    return items


def not_uploaded(name: str, root: Path, excludes: list[str], now: float | None = None) -> int | None:
    """Files iCloud has not uploaded and that were last changed more than 15 min ago; None = unknown."""
    items = _pending_items(name, root, excludes)
    if items is None:
        return None
    cutoff = (now or time.time()) - PENDING_GRACE
    return sum(1 for mtime, rel in items if mtime < cutoff and not excluded(norm(rel), excludes))


def brctl_status() -> str | None:
    hook = _hook()
    if hook is not None:
        return hook.get("brctl", "")
    try:
        res = subprocess.run(["brctl", "status", "com.apple.CloudDocs"], capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return res.stdout if res.returncode == 0 else None


def _stuck_kind(item: str) -> str | None:
    """"up" for a failed upload older than 15 min, "down" for a failed download, else None."""
    kind = None
    for part in PART_RE.split(item)[1:]:
        if "failed" not in part:
            continue
        if part.startswith("upload"):
            age = AGE_RE.search(part)
            if age is None or float(age.group(1)) * AGE_UNIT[age.group(2)] > PENDING_GRACE:
                return "up"
        elif part.startswith("download"):
            kind = "down"
    return kind


def stuck_folders(text: str | None) -> list[tuple[str, str]] | None:
    """(folder, "up"|"down") of every stuck item in `brctl status` output; None when not understood."""
    if text is None:
        return None
    text = ANSI_RE.sub("", text)
    if not HEADER_RE.search(text):
        return None
    out = []
    unders = list(UNDER_RE.finditer(text))
    for i, um in enumerate(unders):
        chunk = text[um.end():unders[i + 1].start() if i + 1 < len(unders) else len(text)]
        starts = [m.start() for m in ITEM_START_RE.finditer(chunk)] + [len(chunk)]
        kinds = (_stuck_kind(chunk[starts[j]:starts[j + 1]]) for j in range(len(starts) - 1))
        out += [(um.group(1).strip(), k) for k in kinds if k]
    return out


def stuck_in(folders: list[tuple[str, str]] | None, root: Path) -> tuple[int, int] | tuple[None, None]:
    """(failed uploads, failed downloads) below the root's folder in the iCloud Drive container."""
    if folders is None:
        return None, None
    top = "/" + root.name  # ~/Documents is /Documents in the iCloud Drive container
    mine = [k for f, k in folders if f == top or f.startswith(top + "/")]
    return mine.count("up"), mine.count("down")


# ── report ──────────────────────────────────────────────────────────────────

@dataclass
class Row:
    node: str
    root: str
    dd: bool | None = None
    not_uploaded: int | None = None
    stuck: int | None = None          # failed uploads (fail the check)
    stuck_down: int | None = None     # failed downloads of evicted files (reported only)
    state: str = ""  # "" = answered; else "unreachable", "docsync too old on …", "error: …"

    @property
    def ok(self) -> bool:
        return not self.state and (not self.dd or (self.not_uploaded == 0 and self.stuck == 0))

    def render(self) -> str:
        if self.state:
            return f"{self.node}  {self.root}  {self.state}"
        line = f"{self.node}  {self.root}  dd: {'on' if self.dd else 'off'}"
        if self.dd:
            num = (lambda n: "unknown" if n is None else str(n))
            stuck = "unknown" if self.stuck is None else f"up {self.stuck} / down {num(self.stuck_down)}"
            line += f"  not uploaded: {num(self.not_uploaded)}  stuck: {stuck}"
        return line

    def to_dict(self) -> dict:
        return {"node": self.node, "root": self.root, "dd": self.dd, "not_uploaded": self.not_uploaded,
                "stuck": self.stuck, "stuck_down": self.stuck_down, "state": self.state or "ok"}


@dataclass
class ICloudReport:
    rows: list[Row] = field(default_factory=list)
    parity: dict[str, list[dict]] = field(default_factory=dict)  # root -> problems (all of them)
    notes: list[str] = field(default_factory=list)
    checked_at: float = field(default_factory=time.time)

    def mixed(self) -> dict[str, list[str]]:
        """root -> nodes with dd off, for roots that are on somewhere else."""
        out = {}
        for root in dict.fromkeys(r.root for r in self.rows):
            answered = [r for r in self.rows if r.root == root and not r.state]
            if any(r.dd for r in answered) and not all(r.dd for r in answered):
                out[root] = [r.node for r in answered if not r.dd]
        return out

    def root_ok(self, root: str) -> bool:
        """Passed among the nodes that answered: same dd everywhere, counts 0, parity clean."""
        rows = [r for r in self.rows if r.root == root and not r.state]
        return root not in self.mixed() and all(r.ok for r in rows) and not self.parity.get(root)

    @property
    def ok(self) -> bool:
        return all(r.ok for r in self.rows) and not self.mixed() and not any(self.parity.values())

    @property
    def exit_code(self) -> int:
        return 0 if self.ok else 1

    def to_dict(self) -> dict:
        from docsync import __version__
        return {"nodes": [r.to_dict() for r in self.rows],
                "parity": {k: v[:SHOW_PROBLEMS] for k, v in self.parity.items()},
                "parity_total": {k: len(v) for k, v in self.parity.items()},
                "notes": list(self.notes), "ok": self.ok, "version": (_hook() or {}).get("version", __version__)}

    def render(self) -> str:
        out = [r.render() for r in self.rows] + self.notes
        for root, probs in self.parity.items():
            if probs:
                out.append(f"parity {root}: {len(probs)} problem{'s' if len(probs) != 1 else ''}")
                out += [f"   {p['path']}  {p['problem']} on {p['node']}" for p in probs[:SHOW_PROBLEMS]]
                if len(probs) > SHOW_PROBLEMS:
                    out.append(f"   … {len(probs) - SHOW_PROBLEMS} more (docsync icloud-check --json)")
        if self.ok:
            out.append("iCloud: complete")
            return "\n".join(out)
        why = []
        off = sorted({n for nodes in self.mixed().values() for n in nodes})
        if off:
            why.append(f"enable Desktop & Documents on {', '.join(off)} {ENABLE_HINT}")
        down = list(dict.fromkeys(f"{r.node} {r.state}" if r.state.startswith("unreachable") else r.state
                                  for r in self.rows if r.state))
        why += down
        busy = sorted({r.node for r in self.rows if not r.state and r.dd and not r.ok})
        if busy:
            why.append(f"not uploaded or stuck on {', '.join(busy)}")
        if any(self.parity.values()):
            why.append("parity problems in " + ", ".join(k for k, v in self.parity.items() if v))
        out.append("iCloud: NOT complete — " + "; ".join(why))
        return "\n".join(out)


class CoveredReport(PeerReport):
    """Peer row of a sync: no host answered, but iCloud carries this root (counts as ok)."""

    def render(self) -> str:
        return f"── {self.peer}{f' [{self.root}]' if self.root else ''} via iCloud\n   {self.error}"


def covered_report(peer: str, root: str) -> CoveredReport:
    rep = CoveredReport(peer=peer, root=root, via="iCloud", error=COVERED)
    rep.finish()
    return rep


# ── checking ────────────────────────────────────────────────────────────────

def local_rows(node: str, roots: list[tuple[str, Path, list[str]]]) -> list[Row]:
    brctl = None  # (folders,) fetched once, and only when some root is managed
    rows = []
    for name, path, excludes in roots:
        path = Path(path).expanduser()
        if not path.is_dir():
            rows.append(Row(node, name, state=f"error: {path} missing"))
            continue
        row = Row(node, name, dd=dd_on(name, path))
        if row.dd:
            if brctl is None:
                brctl = (stuck_folders(brctl_status()),)
            row.not_uploaded = not_uploaded(name, path, excludes)
            row.stuck, row.stuck_down = stuck_in(brctl[0], path)
        rows.append(row)
    return rows


def local_report(roots: list[tuple[str, Path, list[str]]]) -> ICloudReport:
    """This Mac only (the peer side of a check): no parity, no state file."""
    return ICloudReport(rows=local_rows(node_name(), roots))


def probe_peer(p, roots: list[tuple[str, str, list[str]]], ssh_cmd=None) -> list[Row]:
    """The peer's own `icloud-check --local --json`, rows renamed to the peer's name here."""
    def every(state: str) -> list[Row]:
        return [Row(p.name, name, state=state) for name, _, _ in roots]

    too_old = f"docsync too old on {p.name} (needs {MIN_PEER_VERSION})"
    if p.host is None:
        return every("unreachable")
    t = probe.transport(p, roots[0][1], ssh_cmd)
    if not hasattr(t, "ssh"):  # a directory on this Mac
        return local_rows(p.name, [(name, t.root, ex) for name, _, ex in roots])
    args = " ".join("--root " + shlex.quote(f"{name}={path}") for name, path, _ in roots)
    try:
        res = probe.run(t, f"icloud-check --local --json {args}", timeout=900)
    except subprocess.TimeoutExpired:
        return every("error: no answer within 900 s")
    if probe.unknown_command(res):  # a docsync before 1.3.0
        return every(too_old)
    try:
        data = probe.last_json(res.stdout)
        got = {d["root"]: d for d in data["nodes"]}
    except (ValueError, KeyError, TypeError, AttributeError):
        return every("unreachable" if res.returncode == 255 else f"error: {probe.error_text(res)}")
    if probe.version(data.get("version")) < probe.version(MIN_PEER_VERSION):
        return every(too_old)
    rows = []
    for name, _, _ in roots:
        d = got.get(name)
        if d is None:
            rows.append(Row(p.name, name, state="error: root not reported"))
        else:
            rows.append(Row(p.name, name, d.get("dd"), d.get("not_uploaded"), d.get("stuck"), d.get("stuck_down"),
                            "" if d.get("state", "ok") == "ok" else str(d["state"])))
    return rows


def peer_specs(cfg: Config, roots: list[Root], log) -> list:
    from docsync import api
    from docsync.hosts import resolve
    specs = []
    for p in cfg.peers:
        host, via = resolve(p, log)
        specs.append(api.PeerSpec(p.name, host, p.user, via=via, hosts_tried=list(p.hosts),
                                  paths={r.name: cfg.peer_path(p, r) for r in roots}))
    return specs


def check(cfg: Config, peers: list | None = None, *, roots: list[Root] | None = None, state_dir: Path | None = None,
          ssh_cmd=None, log=None) -> ICloudReport:
    """Every node (this Mac + peers, given as api.PeerSpec with resolved hosts or resolved here) and
    root; writes `<state dir>/icloud-<node>.json` for each. `ssh_cmd(peer name)` overrides ssh (tests)."""
    log = log or (lambda *a: None)
    roots = roots or cfg.effective_roots()
    state_dir = state_dir or STATE_DIR
    peers = peer_specs(cfg, roots, log) if peers is None else peers
    me = node_name()
    mine = [(r.name, cfg.for_root(r).local_root, cfg.for_root(r).excludes) for r in roots]
    spec = {p.name: [(r.name, p.paths.get(r.name) or cfg.peer_path(Peer(p.name, []), r), cfg.for_root(r).excludes)
                     for r in roots] for p in peers}
    with ThreadPoolExecutor(max_workers=max(1, len(peers))) as ex:
        futs = [ex.submit(probe_peer, p, spec[p.name], ssh_cmd) for p in peers]
        rep = ICloudReport(rows=local_rows(me, mine) + [r for f in futs for r in f.result()])
    for r in roots:
        rows = [x for x in rep.rows if x.root == r.name and not x.state]
        if rows and not any(x.dd for x in rows):
            rep.notes.append(f"{r.name}: dd off on every node — docsync only")
        elif rows and all(x.dd for x in rows):
            rep.parity[r.name] = _root_parity(cfg, r, me, peers, spec, rep, ssh_cmd)
    _save_states(rep, state_dir, [me] + [p.name for p in peers])
    return rep


def _root_parity(cfg: Config, root: Root, me: str, peers: list, spec: dict, rep: ICloudReport, ssh_cmd) -> list[dict]:
    rc = cfg.for_root(root)
    answered = {x.node for x in rep.rows if x.root == root.name and not x.state}

    def listing(p):
        cloud: set[str] = set()
        path = next(pth for n, pth, _ in spec[p.name] if n == root.name)
        try:
            return p.name, (probe.transport(p, path, ssh_cmd).manifest(rc.excludes, cloud, rc.includes), cloud)
        except (TransportError, OSError, subprocess.SubprocessError, ValueError) as e:  # that node's problem
            return p.name, e
    cloud_here: set[str] = set()
    trees = {me: (scan(rc.local_root, rc.excludes, cloud_here, rc.includes), cloud_here)}
    with ThreadPoolExecutor(max_workers=max(1, len(peers))) as ex:
        for name, got in ex.map(listing, [p for p in peers if p.name in answered]):
            if isinstance(got, Exception):
                row = next(x for x in rep.rows if x.node == name and x.root == root.name)
                row.state = f"error: listing failed: {str(got)[:120]}"
            else:
                trees[name] = got
    return parity(trees, cfg.case_insensitive)


def _save_states(rep: ICloudReport, state_dir: Path, nodes: list[str]) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    for node in nodes:
        roots = {r.root: {"ok": r.ok and rep.root_ok(r.root), "dd": bool(r.dd)} for r in rep.rows if r.node == node}
        rec = {"checked_at": rep.checked_at, "node": node, "roots": roots}
        f = state_dir / f"icloud-{node}.json"
        tmp = f.with_suffix(".tmp")
        tmp.write_text(json.dumps(rec, indent=1))
        os.replace(tmp, f)


def covered(state_dir: Path, local: str, peer: str, root: str, now: float | None = None) -> bool:
    """The last check (at most 24 h old) passed `root` with dd on, on this Mac and on the peer."""
    now = time.time() if now is None else now
    for node in (local, peer):
        try:
            rec = json.loads((Path(state_dir) / f"icloud-{node}.json").read_text())
            r = rec["roots"][root]
            age = now - float(rec["checked_at"])
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            return False
        if not isinstance(r, dict) or not (r.get("ok") is True and r.get("dd") is True and -60 <= age <= STATE_MAX_AGE):
            return False
    return True
