"""Cluster sync through the docsync engine (three-way merge, deletions, conflict copies,
deletion guard, SafetyNet, parallel peers) instead of the ditto newest-wins copy.

maccluster keeps the fabric: cluster.toml peers, Keychain ssh user, the Thunderbolt bridge
address, presets/includes and the exclude file. docsync gets an explicit request per run.
Selection: ``--engine docsync|ditto|auto`` or ``MACCLUSTER_SYNC_ENGINE``; ``auto`` uses docsync
when the docsync-lan package (>= 1.1, api_version 1) is importable, ditto otherwise.
"""

from __future__ import annotations

import os
import re
import socket
from pathlib import Path

from maccluster.domain.models import Node, SyncHomeResult, SyncPeerResult
from maccluster.errors import CliError

ENGINE_ENV = "MACCLUSTER_SYNC_ENGINE"
ENGINES = ("auto", "docsync", "ditto")
MIN_DOCSYNC = (1, 1)
INSTALL_HINT = "pipx inject maccluster <docsync_lan-*.whl> (GitHub release of maf4711/docsync)"

# Never synced by the docsync engine: application bundles are signed, machine-bound and huge
# (a first sync on 2026-11-07 planned 297,000 files from three Xcode.app copies in Downloads).
# Install apps per Mac (App Store, Homebrew casks, xip) instead.
ENGINE_EXCLUDES = ("*.app", "*.xcarchive")

# maccluster policy → docsync policy; the others have no docsync equivalent.
POLICY_MAP = {"newer": "newest", "prefer-local": "local", "prefer-remote": "remote"}


def docsync_version() -> str | None:
    """Installed docsync version if usable as an engine, else None."""
    try:
        import docsync
        from docsync import api
    except Exception:
        return None
    try:
        major, minor = (int(x) for x in docsync.__version__.split(".")[:2])
    except ValueError:
        return None
    if (major, minor) < MIN_DOCSYNC or getattr(api, "API_VERSION", 0) != 1:
        return None
    return docsync.__version__


def ditto_only_reasons(
    *,
    push_only: bool,
    pull_only: bool,
    transport: str | None,
    conflict_policy: str,
    force_icloud: bool,
    identical: bool,
    quick: bool,
    max_files: int | None,
    max_bytes: int | None,
    target: str = "home",
) -> list[str]:
    """Options only the ditto engine implements."""
    why: list[str] = []
    if target == "dev":
        why.append("~/Developer holds git working trees (sync repos with git)")
    if push_only or pull_only:
        why.append("--push-only/--pull-only (docsync is always bidirectional)")
    if transport == "rdma":
        why.append("--transport rdma")
    if conflict_policy not in POLICY_MAP:
        why.append(f"--conflict-policy {conflict_policy}")
    if force_icloud or identical:
        why.append("--force-icloud/--identical")
    if quick or max_files is not None or max_bytes is not None:
        why.append("--quick/--max-files/--max-bytes")
    return why


def resolve_engine(requested: str | None, reasons: list[str]) -> tuple[str, str]:
    """(engine, note). Raises CliError when docsync is demanded but cannot be used."""
    choice = (requested or os.environ.get(ENGINE_ENV) or "auto").strip().lower()
    if choice not in ENGINES:
        raise CliError(f"invalid sync engine {choice!r} (use {'|'.join(ENGINES)})", exit_code=2)
    if choice == "ditto":
        return "ditto", ""
    version = docsync_version()
    if choice == "docsync":
        if version is None:
            raise CliError(
                f"docsync engine not installed (need docsync-lan >= 1.1): {INSTALL_HINT}",
                exit_code=1,
            )
        if reasons:
            raise CliError(f"{'; '.join(reasons)} needs --engine ditto", exit_code=2)
        return "docsync", ""
    if version is None:
        return "ditto", "engine ditto (docsync not installed)"
    if reasons:
        return "ditto", "engine ditto: " + "; ".join(reasons)
    return "docsync", ""


# Trees of git working copies: a three-way file sync would write conflict copies into .git
# (dry run 2026-11-07: 8,459 conflicts in ~/Developer between two Macs). Git syncs these.
GIT_TREES = ("Developer",)
# Per-machine state: keys, known_hosts, cluster.toml, app credentials and caches. Syncing it
# overwrites one Mac's identity with another's (dry run: 723 conflicts in ~/.config with node-a,
# authorized_keys/known_hosts in ~/.ssh). Explicit sub-folders (`--include .config/fish/`) stay allowed.
MACHINE_STATE = (".ssh", ".config")


def drop_git_trees(includes: tuple[str, ...]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """(includes the docsync engine syncs, includes it leaves out): git trees at any depth,
    machine-state folders as a whole (.ssh always, .config unless a sub-folder is named)."""
    kept, dropped = [], []
    for inc in includes:
        parts = inc.strip("/").split("/")
        top = parts[0]
        whole = len(parts) == 1
        if top in GIT_TREES or top == ".ssh" or (top in MACHINE_STATE and whole):
            dropped.append(inc)
        else:
            kept.append(inc)
    return tuple(kept), tuple(dropped)


def root_name(rel: str) -> str:
    """'Library/Application Support/' → 'library-application-support'."""
    return re.sub(r"[^a-z0-9_-]+", "-", rel.strip("/").lower()).strip("-") or "home"


def translate_excludes(patterns: tuple[str, ...] | list[str], root_rel: str) -> list[str]:
    """maccluster patterns (anchored at the synced tree, ``**/`` = anywhere) → docsync patterns
    relative to one root (no '/' = any path component; with '/' = prefix of the relative path)."""
    out: list[str] = []
    base = root_rel.strip("/")
    for raw in patterns:
        p = raw.strip()
        if not p or p.startswith("#"):
            continue
        p = p.rstrip("/")
        while p.startswith("**/"):
            p = p[3:]
            if "/" not in p:
                break
        if "/" not in p:
            out.append(p)
        elif not base:
            out.append(p + "/")
        elif p.startswith(base + "/"):
            out.append(p[len(base) + 1 :] + "/")
        # anchored elsewhere in the tree → irrelevant for this root
    seen: set[str] = set()
    return [x for x in out if not (x in seen or seen.add(x))]


def roots_for(
    local_home: Path, remote_home: str, includes: tuple[str, ...], target: str
) -> list[tuple[str, Path, str, str]]:
    """[(root name, local path, peer path, path relative to the synced tree)]."""
    if not includes:
        name = "developer" if target == "dev" else "home"
        return [(name, local_home, remote_home, "")]
    out = []
    for inc in includes:
        rel = inc.strip("/")
        out.append((root_name(rel), local_home / rel, f"{remote_home.rstrip('/')}/{rel}", rel))
    return out


def _local_ip_assigned(ip: str) -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.bind((ip, 0))
        return True
    except OSError:
        return False


def _hosts(node: Node, via: str) -> list[str]:
    names = [h for h in node.hostnames if h.endswith(".local") and "(" not in h]
    return names if via == "wifi" else [str(node.ip), *names]


def peer_specs(
    api, peers: list[tuple[Node, str]], *, self_ip: str, via: str, default_user: str, log
) -> list:
    """docsync PeerSpecs: Thunderbolt address first (bound to this Mac's bridge IP when it has
    it), then the .local names; docsync's resolver picks the first that answers."""
    specs = []
    for node, ssh_target in peers:
        user = ssh_target.split("@", 1)[0] if "@" in ssh_target else default_user
        hosts = _hosts(node, via)
        host, how = api.resolve_peer(hosts, user, node.id, log=log)
        tb = host == str(node.ip)
        bind = self_ip if tb and self_ip and _local_ip_assigned(self_ip) else None
        specs.append(
            api.PeerSpec(
                node.id,
                host,
                user,
                bind_ip=bind,
                via="Thunderbolt" if tb else (how or "LAN"),
                hosts_tried=hosts,
            )
        )
    return specs


def _peer_result(node: Node, spec, blocks: list[dict]) -> SyncPeerResult:
    ok = bool(blocks) and all(b["ok"] for b in blocks)
    err = "; ".join(b["error"] for b in blocks if b["error"])
    deleted = sum(b["deleted_local"] + b["deleted_remote"] for b in blocks)
    conflicts = sum(len(b["conflicts"]) for b in blocks)
    held = sum(b["deletes_skipped"] for b in blocks)
    warn = [w for b in blocks for w in b["warnings"]]
    msg = (
        f"docsync {len(blocks)} root(s): deleted {deleted}, conflicts {conflicts}"
        + (f", {held} deletions held back" if held else "")
        + (f" — {err}" if err else "")
        + (f" — {warn[0]}" if warn else "")
    )
    moved = sum(b["pushed_files"] + b["pulled_files"] for b in blocks)
    via = "tb" if spec.via.startswith("Thunderbolt") else "wifi"
    return SyncPeerResult(
        peer_id=node.id,
        peer_ip=str(node.ip),
        ssh_target=f"{spec.user}@{spec.host}" if spec.host else "-",
        push_rc=0 if ok else 1,
        pull_rc=0 if ok else 1,
        ok=ok,
        message=msg,
        push_files=sum(b["pushed_files"] for b in blocks),
        pull_files=sum(b["pulled_files"] for b in blocks),
        push_bytes=sum(b["pushed_bytes"] for b in blocks),
        pull_bytes=sum(b["pulled_bytes"] for b in blocks),
        equal=sum(b["unchanged"] for b in blocks),
        safetynet_backed_up=sum(b["safetynet_files"] for b in blocks),
        via=via,
        transport=via if moved else "",
    )


def run(
    *,
    prog,
    local_home: Path,
    remote_home: str,
    peers: list[tuple[Node, str]],
    self_ip: str,
    via: str,
    target: str,
    includes: tuple[str, ...],
    excludes: tuple[str, ...],
    conflict_policy: str,
    dry_run: bool,
    compare_only: bool,
    default_user: str,
    force_deletes: bool = False,
    api=None,
    state_dir: Path | None = None,
) -> SyncHomeResult:
    """One docsync run over every include root and peer. `api` is injectable for tests."""
    if api is None:
        from docsync import api  # noqa: PLC0415 — optional engine
    version = getattr(api, "__version__", None) or docsync_version() or "?"

    def log(*parts):
        prog.note(" ".join(str(p) for p in parts))

    roots = roots_for(local_home, remote_home, includes, target)
    missing = [str(p) for _, p, _, _ in roots if not p.is_dir()]
    roots = [r for r in roots if r[1].is_dir()]
    for m in missing:
        prog.note(f"  skip root (not on this Mac): {m}")
    if not roots:
        raise CliError("no include root exists on this Mac: " + ", ".join(missing), exit_code=1)
    specs = peer_specs(api, peers, self_ip=self_ip, via=via, default_user=default_user, log=log)
    defaults = list(api.SyncRequest.__dataclass_fields__["excludes"].default_factory()) + list(
        ENGINE_EXCLUDES
    )
    req = api.SyncRequest(
        roots=[
            api.RootSpec(name, str(lp), rp, defaults + translate_excludes(excludes, rel))
            for name, lp, rp, rel in roots
        ],
        peers=specs,
        conflict_policy=POLICY_MAP[conflict_policy],
        force_deletes=force_deletes,
        dry_run=dry_run or compare_only,
        log=log,
        state_dir=state_dir,
    )
    prog.note(f"engine docsync {version}: {len(req.roots)} root(s) × {len(specs)} peer(s)")
    res = api.sync(req)
    for line in res.report.render().splitlines():
        prog.note(line)
    if res.error:
        raise CliError(f"docsync: {res.error}", exit_code=res.exit_code or 1)
    d = res.to_dict()
    by_peer: dict[str, list[dict]] = {}
    for block in d["peers"]:
        by_peer.setdefault(block["peer"], []).append(block)
    nodes = {n.id: n for n, _ in peers}
    results = tuple(_peer_result(nodes[s.name], s, by_peer.get(s.name, [])) for s in specs)
    return SyncHomeResult(
        local_home=str(local_home),
        dry_run=dry_run or compare_only,
        strategy=f"docsync {version} three-way ({POLICY_MAP[conflict_policy]})",
        peers=results,
        excludes=tuple(excludes),
        includes=tuple(includes),
        conflict_policy=conflict_policy,
        compare_only=compare_only,
        safetynet=True,
        target=target,
        transport_priority=("tb", "wifi"),
    )


# ── shipping the engine to peers (remote-install) ─────────────────────────────


def find_docsync_wheel() -> Path | None:
    """docsync-lan wheel to ship to peers: $MACCLUSTER_DOCSYNC_WHEEL, else the newest in
    ~/Library/Caches/maccluster/wheels, else ~/Developer/docsync/dist. None if there is none."""
    env = os.environ.get("MACCLUSTER_DOCSYNC_WHEEL")
    if env and Path(env).is_file():
        return Path(env)
    found: list[Path] = []
    for d in (
        Path.home() / "Library" / "Caches" / "maccluster" / "wheels",
        Path(os.environ.get("MACCLUSTER_DOCSYNC_SRC", Path.home() / "Developer" / "docsync"))
        / "dist",
    ):
        if d.is_dir():
            found += d.glob("docsync_lan-*-py3-none-any.whl")
    if not found:
        return None

    def ver(p: Path) -> tuple[int, ...]:
        try:
            return tuple(int(x) for x in p.name.split("-")[1].split("."))
        except ValueError:
            return (0,)

    return max(found, key=ver)


def inject_script(remote_wheel: str, digest: str) -> str:
    """Peer-side bash: verify the wheel digest, install it next to maccluster, print the version."""
    import shlex

    w, d = shlex.quote(remote_wheel), shlex.quote(digest)
    return (
        'set -euo pipefail; PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:$PATH"; '
        f'[[ "$(shasum -a 256 {w} | cut -d" " -f1)" == {d} ]] || {{ echo "docsync wheel digest mismatch"; exit 1; }}; '
        "if command -v pipx >/dev/null 2>&1 && pipx list --short 2>/dev/null | grep -q '^maccluster '; then "
        f"pipx inject --force maccluster {w}; "
        f'else "$HOME/.local/share/maccluster/venv/bin/python" -m pip install --no-input --force-reinstall {w}; fi; '
        "cli=$(python3 -c 'import os,sys; print(os.path.dirname(os.path.realpath(sys.argv[1])))' \"$(command -v maccluster)\"); "
        '"$cli/python" -c \'import docsync; print("docsync", docsync.__version__)\''
    )
