"""Wi-Fi pass for `maccluster sync dev`: recent git repos over .local SSH."""

from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path

from maccluster.domain.models import Node, SyncHomeResult
from maccluster.services.sync_filters import matches_include

_GIT_MARKERS = ("HEAD", "index", "COMMIT_EDITMSG", "FETCH_HEAD")


def _mtime_ns(path: Path) -> int:
    try:
        return path.lstat().st_mtime_ns
    except OSError:
        return 0


def _repo_activity_ns(repo: Path, git: Path) -> int:
    ns = _mtime_ns(repo)
    ns = max(ns, _mtime_ns(git))
    if git.is_dir():
        for name in _GIT_MARKERS:
            ns = max(ns, _mtime_ns(git / name))
    return ns


def list_recent_repos(root: Path | str, limit: int = 10) -> tuple[str, ...]:
    """Top-level git repos under *root*, newest git activity first.

    A repo is a directory that contains ``.git`` (directory or gitfile).
    Ranking uses cheap git metadata mtimes (HEAD/index/…), not a full tree walk.
    """
    if limit <= 0:
        return ()
    base = Path(root)
    if not base.is_dir():
        return ()
    scored: list[tuple[int, str]] = []
    try:
        entries = os.scandir(base)
    except OSError:
        return ()
    with entries:
        for ent in entries:
            name = ent.name
            if name.startswith("."):
                continue
            try:
                if not ent.is_dir(follow_symlinks=False):
                    continue
            except OSError:
                continue
            git = Path(ent.path) / ".git"
            try:
                if not git.exists():
                    continue
            except OSError:
                continue
            ns = _repo_activity_ns(Path(ent.path), git)
            scored.append((ns, name))
    scored.sort(key=lambda row: (-row[0], row[1].lower()))
    return tuple(name for _, name in scored[:limit])


def wifi_hostname(node: Node) -> str | None:
    """First Bonjour ``*.local`` hostname from cluster.toml, if any."""
    for raw in node.hostnames:
        host = str(raw).strip()
        if host.lower().endswith(".local"):
            return host
    return None


def wifi_ssh_target(node: Node, *, default_user: str) -> str | None:
    """SSH target for the Wi-Fi pass: ``user@host.local``, never the TB IP.

    ``node.ssh_target`` may be ``user@10.42.0.x``; that IP is TB-only and must
    not be reused here. The user part is kept.
    """
    host = wifi_hostname(node)
    if not host:
        return None
    user = (default_user or "").strip()
    tgt = (node.ssh_target or "").strip()
    if "@" in tgt:
        left = tgt.split("@", 1)[0].strip()
        if left:
            user = left
    if not user:
        return None
    return f"{user}@{host}"


def intersect_repos_with_includes(
    repos: tuple[str, ...],
    includes: tuple[str, ...],
) -> tuple[str, ...]:
    """Keep recent repos that fall under user ``--include`` roots (if any)."""
    if not includes:
        return repos
    kept: list[str] = []
    for repo in repos:
        if matches_include(repo, includes):
            kept.append(repo)
            continue
        if any(
            inc.rstrip("/").startswith(repo + "/") or inc.rstrip("/") == repo for inc in includes
        ):
            kept.append(repo)
    return tuple(kept)


def merge_sync_results(first: SyncHomeResult, *rest: SyncHomeResult) -> SyncHomeResult:
    """Concatenate TB + Wi-Fi peer rows into one result."""
    peers = list(first.peers)
    includes = list(first.includes)
    wifi_repos = list(getattr(first, "wifi_repos", ()) or ())
    log_path = first.log_path
    for extra in rest:
        peers.extend(extra.peers)
        for inc in extra.includes:
            if inc not in includes:
                includes.append(inc)
        for repo in getattr(extra, "wifi_repos", ()) or ():
            if repo not in wifi_repos:
                wifi_repos.append(repo)
        if extra.log_path:
            log_path = extra.log_path
    n_wifi = sum(1 for p in peers if getattr(p, "via", "tb") == "wifi")
    strategy = first.strategy
    if n_wifi:
        n = len(wifi_repos) or n_wifi
        strategy = f"{first.strategy} + wifi top-{n}"
    return replace(
        first,
        peers=tuple(peers),
        includes=tuple(includes),
        wifi_repos=tuple(wifi_repos),
        log_path=log_path,
        strategy=strategy,
    )
