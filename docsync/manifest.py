"""File manifests: relpath -> (mtime, size). Used for 3-way sync decisions."""

from __future__ import annotations

import fnmatch
import json
import os
import unicodedata
from pathlib import Path

Entry = tuple[int, int]  # (mtime seconds, size bytes)
Manifest = dict[str, Entry]


def norm(path: str) -> str:
    """Manifest keys are NFC so 'Köln' typed on one Mac equals 'Köln' decomposed on another.
    APFS lookups are normalization-insensitive, so the NFC key is valid for rsync on both sides."""
    return unicodedata.normalize("NFC", path)


def excluded(relpath: str, patterns: list[str]) -> bool:
    """True if any path component (or the full relpath) matches a pattern."""
    parts = relpath.split("/")
    for pat in patterns:
        if "/" in pat:
            if fnmatch.fnmatch(relpath, pat.strip("/")) or relpath.startswith(pat.strip("/") + "/"):
                return True
            continue
        for part in parts:
            if fnmatch.fnmatch(part, pat):
                return True
    return False


def scan(root: Path, patterns: list[str]) -> Manifest:
    """Walk root, return manifest of regular files (symlinks skipped)."""
    out: Manifest = {}
    root = root.expanduser()
    if not root.is_dir():
        return out
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = os.path.relpath(dirpath, root)
        rel_dir = "" if rel_dir == "." else rel_dir
        keep = []
        for d in dirnames:
            rel = f"{rel_dir}/{d}" if rel_dir else d
            if not excluded(rel, patterns) and not os.path.islink(os.path.join(dirpath, d)):
                keep.append(d)
        dirnames[:] = keep
        for f in filenames:
            rel = f"{rel_dir}/{f}" if rel_dir else f
            if excluded(rel, patterns):
                continue
            full = os.path.join(dirpath, f)
            try:
                st = os.lstat(full)
            except OSError:
                continue
            if not os.path.isfile(full) or os.path.islink(full):
                continue
            out[norm(rel)] = (int(st.st_mtime), int(st.st_size))
    return out


def parse_lines(text: str, patterns: list[str]) -> Manifest:
    """Parse 'mtime<TAB>size<TAB>relpath' records (remote lister output).
    Records are NUL-separated (python lister) or newline-separated (stat fallback)."""
    out: Manifest = {}
    records = text.split("\0") if "\0" in text else text.splitlines()
    for line in records:
        if not line.strip():
            continue
        try:
            mtime, size, rel = line.split("\t", 2)
        except ValueError:
            continue
        rel = norm(rel[2:] if rel.startswith("./") else rel)
        if excluded(rel, patterns):
            continue
        out[rel] = (int(float(mtime)), int(size))
    return out


def load(file: Path) -> Manifest | None:
    if not file.exists():
        return None
    data = json.loads(file.read_text())
    return {k: (int(v[0]), int(v[1])) for k, v in data.get("files", {}).items()}


def save(file: Path, manifest: Manifest, meta: dict | None = None) -> None:
    file.parent.mkdir(parents=True, exist_ok=True)
    payload = {"version": 1, "meta": meta or {}, "files": {k: list(v) for k, v in sorted(manifest.items())}}
    tmp = file.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=1))
    os.replace(tmp, file)


# Remote lister: runs under `python3 -` on the peer; prints mtime\tsize\trelpath NUL-terminated
# (file names may contain newlines).
LISTER_PY = r'''
import os, sys
root = os.path.expanduser(sys.argv[1])
out = sys.stdout
for dirpath, dirnames, filenames in os.walk(root):
    dirnames[:] = [d for d in dirnames if not os.path.islink(os.path.join(dirpath, d))]
    for f in filenames:
        full = os.path.join(dirpath, f)
        if os.path.islink(full) or not os.path.isfile(full):
            continue
        try:
            st = os.lstat(full)
        except OSError:
            continue
        rel = os.path.relpath(full, root)
        out.write("%d\t%d\t%s\0" % (int(st.st_mtime), st.st_size, rel))
'''

# Fallback when the peer has no python3: BSD stat (macOS), then GNU stat.
LISTER_SH = (
    'cd {root} && ( find . -type f -exec stat -f "%m%t%z%t%N" {{}} + 2>/dev/null '
    '|| find . -type f -exec stat -c "%Y%t%s%t%n" {{}} + )'
)
