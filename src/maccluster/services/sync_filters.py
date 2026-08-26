"""CCC-style filters: presets, includes, exclude-from file."""

from __future__ import annotations

from pathlib import Path

from maccluster.constants import SYNC_PATH_PRESETS
from maccluster.errors import CliError


def load_exclude_file(path: Path | None) -> tuple[str, ...]:
    """Load exclude patterns (one per line; # comments; blank skipped)."""
    if path is None:
        return ()
    p = path.expanduser()
    if not p.is_file():
        return ()
    out: list[str] = []
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        out.append(s)
    return tuple(out)


def resolve_presets(names: tuple[str, ...] | list[str] | None) -> tuple[str, ...]:
    """Map preset names to include path prefixes under $HOME."""
    if not names:
        return ()
    includes: list[str] = []
    unknown: list[str] = []
    for raw in names:
        for part in str(raw).split(","):
            key = part.strip().lower()
            if not key:
                continue
            roots = SYNC_PATH_PRESETS.get(key)
            if roots is None:
                unknown.append(key)
                continue
            includes.extend(roots)
    if unknown:
        known = ", ".join(sorted(SYNC_PATH_PRESETS))
        raise CliError(
            f"unknown --preset {unknown!r}; known: {known}",
            exit_code=2,
        )
    # de-dupe preserve order
    seen: set[str] = set()
    ordered: list[str] = []
    for inc in includes:
        if inc not in seen:
            seen.add(inc)
            ordered.append(inc)
    return tuple(ordered)


def merge_includes(
    presets: tuple[str, ...] | list[str] | None,
    explicit: tuple[str, ...] | list[str] | None,
) -> tuple[str, ...]:
    from_preset = resolve_presets(presets)
    extra = tuple(x.strip().replace("\\", "/") for x in (explicit or ()) if x and x.strip())
    # normalize trailing slash for directories
    normed: list[str] = []
    seen: set[str] = set()
    for inc in (*from_preset, *extra):
        n = inc.replace("\\", "/").lstrip("/")
        if not n:
            continue
        if n not in seen:
            seen.add(n)
            normed.append(n)
    return tuple(normed)


def matches_include(rel: str, includes: tuple[str, ...]) -> bool:
    """If includes empty → all paths; else path must be under an include root."""
    if not includes:
        return True
    rel = rel.replace("\\", "/").lstrip("/")
    for inc in includes:
        base = inc.rstrip("/")
        if rel == base or rel.startswith(base + "/"):
            return True
        # include as file pattern
        if "/" not in base.rstrip("/") and rel == base:
            return True
    return False


def filter_inventory(
    inv: dict,
    includes: tuple[str, ...],
) -> dict:
    if not includes:
        return inv
    return {k: v for k, v in inv.items() if matches_include(k, includes)}
