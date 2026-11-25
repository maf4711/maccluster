"""Cross-node parity for `docsync icloud-check`: same paths, same size/mtime, no iCloud duplicates."""

from __future__ import annotations

import re

MTIME_SLACK = 2  # seconds
DUPLICATE_RE = re.compile(r"^(.+) 2(\.[^./ ]+)?$")


def parity(trees: dict[str, tuple[dict, set[str]]], case_insensitive: bool = True) -> list[dict]:
    """trees: node -> (manifest of local files, cloud-only paths). Problems sorted by path."""
    fold = (lambda s: s.casefold()) if case_insensitive else (lambda s: s)
    present = {n: {fold(p): p for p in (*m, *cloud)} for n, (m, cloud) in trees.items()}
    local = {n: {fold(p): v for p, v in m.items()} for n, (m, _) in trees.items()}
    probs = []
    for key in sorted(set().union(*present.values()) if present else ()):
        path = next(present[n][key] for n in present if key in present[n])
        probs += [{"path": path, "node": n, "problem": "missing"} for n in present if key not in present[n]]
        have = [n for n in local if key in local[n]]
        for n in have[1:]:
            (m0, s0), (m1, s1) = local[have[0]][key], local[n][key]
            if s0 != s1 or abs(m0 - m1) > MTIME_SLACK:
                probs.append({"path": path, "node": n, "problem": "differs"})
    for n, (m, _) in trees.items():
        for p, (_, size) in m.items():
            d, _, base = p.rpartition("/")
            hit = DUPLICATE_RE.match(base)
            orig = hit and ((d + "/" if d else "") + hit.group(1) + (hit.group(2) or ""))
            if orig and orig in m and m[orig][1] == size:
                probs.append({"path": p, "node": n, "problem": "icloud duplicate"})
    return sorted(probs, key=lambda x: (x["path"], x["node"]))
