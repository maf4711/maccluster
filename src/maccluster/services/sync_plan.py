"""Planning side of the home sync: inventory diff, conflict policy, batching.

Extracted verbatim from ``sync_service``. Everything here is pure (no I/O):
``plan_transfers`` / ``precise_delta`` turn two inventories into push/pull
lists, ``apply_batch_limits`` / ``_chunk_rels`` / ``_split_large_files`` cap
and slice those lists for the ditto/scp transfer functions.
"""

from __future__ import annotations

from dataclasses import dataclass

from maccluster.render.progress import format_bytes
from maccluster.services.sync_inventory import FileMeta


def plan_transfers(
    local: dict[str, FileMeta],
    remote: dict[str, FileMeta],
    *,
    policy: str = "newer",
    remote_complete: bool = True,
) -> tuple[list[str], list[str], dict[str, int]]:
    """
    Return (to_push, to_pull, stats).

    ``remote_complete`` says whether the remote inventory covers the whole
    tree. When the remote walk stopped early (time budget, hung directory),
    a file missing from *remote* means "not looked at", not "not there" —
    pushing it would re-copy data the peer already has. Those land in
    ``stats["remote_unknown"]`` and are left for the next run instead.

    Policies (CCC-inspired):
      newer          — mtime newest-wins (default)
      larger         — larger size wins (mtime tie-break)
      prefer-local   — on conflict always push local
      prefer-remote  — on conflict always pull remote
      skip-conflict  — only missing files; never overwrite
    """
    to_push: list[str] = []
    to_pull: list[str] = []
    stats = {
        "only_local": 0,
        "only_remote": 0,
        "remote_unknown": 0,
        "local_newer": 0,
        "remote_newer": 0,
        "equal": 0,
        "conflicts_skipped": 0,
    }
    all_rels = set(local) | set(remote)
    for rel in all_rels:
        lm = local.get(rel)
        rm = remote.get(rel)
        if lm is not None and rm is None:
            if not remote_complete:
                # Unlisted under a truncated walk: unknown, not absent.
                stats["remote_unknown"] += 1
                continue
            to_push.append(rel)
            stats["only_local"] += 1
            continue
        if rm is not None and lm is None:
            to_pull.append(rel)
            stats["only_remote"] += 1
            continue
        if lm is None or rm is None:
            continue
        # both exist
        same = lm.mtime_ns == rm.mtime_ns and lm.size == rm.size
        if same or (lm.mtime_ns == rm.mtime_ns and policy == "newer"):
            if lm.mtime_ns == rm.mtime_ns and lm.size == rm.size:
                stats["equal"] += 1
                continue
            if lm.mtime_ns == rm.mtime_ns and policy == "newer":
                stats["equal"] += 1
                continue

        if policy == "skip-conflict":
            stats["conflicts_skipped"] += 1
            continue

        if policy == "prefer-local":
            if lm.mtime_ns != rm.mtime_ns or lm.size != rm.size:
                to_push.append(rel)
                if lm.mtime_ns >= rm.mtime_ns:
                    stats["local_newer"] += 1
                else:
                    stats["local_newer"] += 1  # forced
            continue

        if policy == "prefer-remote":
            if lm.mtime_ns != rm.mtime_ns or lm.size != rm.size:
                to_pull.append(rel)
                stats["remote_newer"] += 1
            continue

        if policy == "larger":
            if lm.size > rm.size:
                to_push.append(rel)
                stats["local_newer"] += 1
            elif rm.size > lm.size:
                to_pull.append(rel)
                stats["remote_newer"] += 1
            elif lm.mtime_ns > rm.mtime_ns:
                to_push.append(rel)
                stats["local_newer"] += 1
            elif rm.mtime_ns > lm.mtime_ns:
                to_pull.append(rel)
                stats["remote_newer"] += 1
            else:
                stats["equal"] += 1
            continue

        # newer (default)
        if lm.mtime_ns > rm.mtime_ns:
            to_push.append(rel)
            stats["local_newer"] += 1
        elif rm.mtime_ns > lm.mtime_ns:
            to_pull.append(rel)
            stats["remote_newer"] += 1
        else:
            stats["equal"] += 1

    to_push.sort()
    to_pull.sort()
    return to_push, to_pull, stats


def apply_batch_limits(
    to_push: list[str],
    to_pull: list[str],
    push_sizes: dict[str, int],
    pull_sizes: dict[str, int],
    *,
    max_files: int | None,
    max_bytes: int | None,
) -> tuple[list[str], list[str], bool]:
    """Cap transfer lists; prefer smaller files first so many finish per run."""
    if max_files is None and max_bytes is None:
        return to_push, to_pull, False

    # Merge candidates ordered by size, tag direction
    cands: list[tuple[int, str, str]] = []
    for r in to_push:
        cands.append((push_sizes.get(r, 0), "push", r))
    for r in to_pull:
        cands.append((pull_sizes.get(r, 0), "pull", r))
    cands.sort(key=lambda t: (t[0], t[1], t[2]))

    out_push: list[str] = []
    out_pull: list[str] = []
    files = 0
    bytes_ = 0
    for sz, direction, rel in cands:
        if max_files is not None and files >= max_files:
            return sorted(out_push), sorted(out_pull), True
        if max_bytes is not None and files > 0 and bytes_ + sz > max_bytes:
            return sorted(out_push), sorted(out_pull), True
        if direction == "push":
            out_push.append(rel)
        else:
            out_pull.append(rel)
        files += 1
        bytes_ += sz
    truncated = len(out_push) + len(out_pull) < len(to_push) + len(to_pull)
    return sorted(out_push), sorted(out_pull), truncated


def classify_compare(
    local: dict[str, FileMeta],
    remote: dict[str, FileMeta],
) -> dict[str, list[str]]:
    """Buckets for --compare (no transfer)."""
    only_local: list[str] = []
    only_remote: list[str] = []
    local_newer: list[str] = []
    remote_newer: list[str] = []
    equal: list[str] = []
    for rel in sorted(set(local) | set(remote)):
        lm, rm = local.get(rel), remote.get(rel)
        if lm and not rm:
            only_local.append(rel)
        elif rm and not lm:
            only_remote.append(rel)
        elif lm and rm:
            if lm.mtime_ns > rm.mtime_ns:
                local_newer.append(rel)
            elif rm.mtime_ns > lm.mtime_ns:
                remote_newer.append(rel)
            else:
                equal.append(rel)
    return {
        "only_local": only_local,
        "only_remote": only_remote,
        "local_newer": local_newer,
        "remote_newer": remote_newer,
        "equal": equal,
    }


@dataclass(frozen=True)
class DeltaBucket:
    """One inventory-diff bucket with count + total bytes + sample paths."""

    count: int
    bytes: int
    samples: tuple[str, ...] = ()


@dataclass(frozen=True)
class PreciseDelta:
    """Inventory → compare result: exact file deltas, not bulk size guesses.

    Built from local/remote inventories (relpath → mtime_ns + size) and the
    same conflict policy as ``plan_transfers``. Transfer lists are the only
    payload that should be synced (difference only).
    """

    policy: str
    local_files: int
    remote_files: int
    local_bytes: int
    remote_bytes: int
    only_local: DeltaBucket
    only_remote: DeltaBucket
    local_newer: DeltaBucket
    remote_newer: DeltaBucket
    equal: DeltaBucket
    conflicts_skipped: int
    to_push: tuple[str, ...]
    to_pull: tuple[str, ...]
    push_bytes: int
    pull_bytes: int

    @property
    def delta_files(self) -> int:
        return len(self.to_push) + len(self.to_pull)

    @property
    def delta_bytes(self) -> int:
        return self.push_bytes + self.pull_bytes

    @property
    def in_sync(self) -> bool:
        return self.delta_files == 0


def _bucket_from(
    rels: list[str],
    inv: dict[str, FileMeta],
    *,
    sample: int = 8,
) -> DeltaBucket:
    total = 0
    for r in rels:
        m = inv.get(r)
        if m is not None:
            total += max(0, int(m.size))
    samples = tuple(f"{r} ({format_bytes(inv[r].size)})" if r in inv else r for r in rels[:sample])
    return DeltaBucket(count=len(rels), bytes=total, samples=samples)


def precise_delta(
    local: dict[str, FileMeta],
    remote: dict[str, FileMeta],
    *,
    policy: str = "newer",
    sample: int = 8,
) -> PreciseDelta:
    """Read two inventories, classify exact deltas, plan difference transfer.

    Pure function — no I/O. Prefer this over bulk ``du``/full-tree copies:
    only missing/newer files (by policy) enter ``to_push`` / ``to_pull``.
    """
    buckets = classify_compare(local, remote)
    to_push, to_pull, stats = plan_transfers(local, remote, policy=policy)
    push_bytes = sum(max(0, int(local[r].size)) for r in to_push if r in local)
    pull_bytes = sum(max(0, int(remote[r].size)) for r in to_pull if r in remote)
    return PreciseDelta(
        policy=policy,
        local_files=len(local),
        remote_files=len(remote),
        local_bytes=sum(max(0, int(m.size)) for m in local.values()),
        remote_bytes=sum(max(0, int(m.size)) for m in remote.values()),
        only_local=_bucket_from(buckets["only_local"], local, sample=sample),
        only_remote=_bucket_from(buckets["only_remote"], remote, sample=sample),
        local_newer=_bucket_from(buckets["local_newer"], local, sample=sample),
        remote_newer=_bucket_from(buckets["remote_newer"], remote, sample=sample),
        equal=_bucket_from(buckets["equal"], local, sample=sample),
        conflicts_skipped=int(stats.get("conflicts_skipped", 0)),
        to_push=tuple(to_push),
        to_pull=tuple(to_pull),
        push_bytes=push_bytes,
        pull_bytes=pull_bytes,
    )


def format_precise_delta(
    delta: PreciseDelta,
    *,
    peer_id: str,
    peer_ip: str = "",
) -> list[str]:
    """Human-readable lines for one peer delta report."""
    where = f"{peer_id}" + (f" ({peer_ip})" if peer_ip else "")
    lines = [
        f"delta vs {where}  policy={delta.policy}",
        f"  inventory: local={delta.local_files:,} files "
        f"({format_bytes(delta.local_bytes)}) · "
        f"remote={delta.remote_files:,} files ({format_bytes(delta.remote_bytes)})",
        f"  buckets: only_local={delta.only_local.count:,}/"
        f"{format_bytes(delta.only_local.bytes)}  "
        f"only_remote={delta.only_remote.count:,}/"
        f"{format_bytes(delta.only_remote.bytes)}  "
        f"local_newer={delta.local_newer.count:,}/"
        f"{format_bytes(delta.local_newer.bytes)}  "
        f"remote_newer={delta.remote_newer.count:,}/"
        f"{format_bytes(delta.remote_newer.bytes)}  "
        f"equal={delta.equal.count:,}",
        f"  plan: push {len(delta.to_push):,} files "
        f"({format_bytes(delta.push_bytes)}) · "
        f"pull {len(delta.to_pull):,} files ({format_bytes(delta.pull_bytes)}) · "
        f"delta_total={format_bytes(delta.delta_bytes)}",
    ]
    if delta.conflicts_skipped:
        lines.append(f"  conflicts_skipped={delta.conflicts_skipped:,}")
    if delta.in_sync:
        lines.append("  status: in sync (no delta)")
    else:
        lines.append(f"  status: {delta.delta_files:,} files differ")
    if delta.only_local.samples or delta.local_newer.samples:
        for s in (delta.only_local.samples + delta.local_newer.samples)[:6]:
            lines.append(f"    push + {s}")
        extra = len(delta.to_push) - min(6, len(delta.to_push))
        if extra > 0:
            lines.append(f"    push … +{extra} more")
    if delta.only_remote.samples or delta.remote_newer.samples:
        for s in (delta.only_remote.samples + delta.remote_newer.samples)[:6]:
            lines.append(f"    pull + {s}")
        extra = len(delta.to_pull) - min(6, len(delta.to_pull))
        if extra > 0:
            lines.append(f"    pull … +{extra} more")
    return lines


# Large ditto CPIO archives (>~2–4 GiB) often fail with "cpio read error" after scp.
# Auto-split into smaller batches.
SYNC_CHUNK_BYTES = 2 * 1024 * 1024 * 1024  # 2 GiB payload per archive
SYNC_CHUNK_FILES = 120
# Single files above this skip CPIO (ditto -c/-x often fails ~10+ GiB with "cpio read error")
SYNC_LARGE_FILE_BYTES = 3 * 1024 * 1024 * 1024  # 3 GiB → direct scp


def _chunk_rels(
    rels: list[str],
    sizes: dict[str, int],
    *,
    max_bytes: int = SYNC_CHUNK_BYTES,
    max_files: int = SYNC_CHUNK_FILES,
) -> list[list[str]]:
    """Split transfer list into size/count-limited batches (preserves order)."""
    if not rels:
        return []
    batches: list[list[str]] = []
    cur: list[str] = []
    cur_b = 0
    for r in rels:
        sz = int(sizes.get(r, 0) or 0)
        if cur and (len(cur) >= max_files or (max_bytes > 0 and cur_b + sz > max_bytes)):
            batches.append(cur)
            cur = []
            cur_b = 0
        cur.append(r)
        cur_b += sz
    if cur:
        batches.append(cur)
    return batches


def _bytes_for_rels(inv: dict[str, FileMeta], rels: list[str]) -> int:
    return sum(inv[r].size for r in rels if r in inv)


def _sample_list(rels: list[str], *, label: str) -> str:
    sample = "\n".join(f"  + {r}" for r in rels[:30])
    more = f"\n  … +{len(rels) - 30} more" if len(rels) > 30 else ""
    return f"{label}: {len(rels)} files\n{sample}{more}"


def _split_large_files(rels: list[str], sizes: dict[str, int]) -> tuple[list[str], list[str]]:
    """Return (normal_rels, large_rels) where large is direct-scp territory."""
    normal: list[str] = []
    large: list[str] = []
    for r in rels:
        if int(sizes.get(r, 0) or 0) >= SYNC_LARGE_FILE_BYTES:
            large.append(r)
        else:
            normal.append(r)
    return normal, large
