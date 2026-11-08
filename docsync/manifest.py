"""File manifests: relpath -> (mtime, size). Used for 3-way sync decisions."""

from __future__ import annotations

import fnmatch
import json
import os
import unicodedata
from pathlib import Path

from docsync.config import DENY_PATTERNS

Entry = tuple[int, int]  # (mtime seconds, size bytes)
Manifest = dict[str, Entry]
SF_DATALESS = 0x40000000  # macOS: file is evicted to iCloud; reading it triggers a download


def norm(path: str) -> str:
    """Manifest keys are NFC so 'Köln' typed on one Mac equals 'Köln' decomposed on another.
    APFS lookups are normalization-insensitive, so the NFC key is valid for rsync on both sides."""
    return unicodedata.normalize("NFC", path)


_MATCHER_CACHE: dict[tuple, object] = {}


def _matcher(patterns: list[str]):
    """Compile the component patterns into one regex; '/'-patterns stay prefix checks."""
    key = tuple(patterns)
    hit = _MATCHER_CACHE.get(key)
    if hit is None:
        import re
        comp = [fnmatch.translate(p) for p in patterns if "/" not in p]
        rx = re.compile("|".join(f"(?:{c})" for c in comp)) if comp else None
        prefixes = [p.strip("/") for p in patterns if "/" in p]
        hit = (rx, prefixes)
        _MATCHER_CACHE[key] = hit
    return hit


def excluded(relpath: str, patterns: list[str]) -> bool:
    """True if any path component (or the full relpath) matches a pattern."""
    rx, prefixes = _matcher(patterns)
    if rx is not None:
        for part in relpath.split("/"):
            if rx.match(part):
                return True
    for pre in prefixes:
        if fnmatch.fnmatch(relpath, pre) or relpath.startswith(pre + "/"):
            return True
    return False


def _excluded_slow(relpath: str, patterns: list[str]) -> bool:  # reference implementation (tests)
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


# Include allowlist (anchored at the root; `*`/`?` never cross '/', `**` = any depth) and the deny
# list for roots with includes. One Python 3.9 source: exec'd here and prepended to LISTER_PY.
FILTER_PY = r"""
import fnmatch, re


def _seg_rx(seg, flags=0):
    out, i = "", 0
    while i < len(seg):
        c = seg[i]
        if c == "*":
            out += "[^/]*"
        elif c == "?":
            out += "[^/]"
        elif c == "[" and "]" in seg[i + 2:]:
            j = seg.index("]", i + 2)
            body = seg[i + 1:j].replace("\\", "\\\\")
            out += "[" + ("^" + body[1:] if body.startswith("!") else body) + "]"
            i = j
        else:
            out += re.escape(c)
        i += 1
    return re.compile(out + r"\Z", flags)


def _compile(pattern, flags=0):
    segs = []
    for s in pattern.strip("/").split("/"):
        if s == "**":
            if not segs or segs[-1] is not None:
                segs.append(None)
        elif s:
            segs.append(_seg_rx(s, flags))
    return segs


def _match(ps, xs):
    if not ps:
        return not xs
    if ps[0] is None:
        if len(ps) == 1:
            return len(xs) > 0
        return any(_match(ps[1:], xs[k:]) for k in range(len(xs) + 1))
    return bool(xs) and ps[0].match(xs[0]) is not None and _match(ps[1:], xs[1:])


def _below(ps, xs):
    if not xs:
        return bool(ps)
    if not ps:
        return False
    if ps[0] is None:
        return True
    return ps[0].match(xs[0]) is not None and _below(ps[1:], xs[1:])


def _unconflict(name):
    return re.sub(r"\.conflict-[^.]+", "", name)


class Filter:
    def __init__(self, includes, deny, icase=False):
        flags = re.IGNORECASE if icase else 0
        self.pats = [_compile(p, flags) for p in includes]
        self.deny = [d.lower() for d in deny]

    def wants(self, rel):
        parts = rel.split("/")
        if any(_match(p, parts) for p in self.pats):
            return True
        # a conflict copy (engine.conflict_name) is tracked like its original file
        orig = _unconflict(parts[-1])
        return orig != parts[-1] and any(_match(p, parts[:-1] + [orig]) for p in self.pats)

    def descend(self, rel_dir):
        parts = rel_dir.split("/")
        return any(_below(p, parts) for p in self.pats)

    def denied(self, rel):
        # a conflict copy of a denied name (.credentials.conflict-<stamp>-<host>.json) is denied too
        return any(fnmatch.fnmatchcase(x.lower(), d) for part in rel.split("/")
                   for x in (part, _unconflict(part)) for d in self.deny)
"""
_NS: dict = {}
exec(compile(FILTER_PY, "<docsync-filter>", "exec"), _NS)
Filter = _NS["Filter"]
compile_include = _NS["_compile"]  # raises re.error for a bad pattern (config validates with it)


def include_filter(includes: list[str] | None, icase: bool = False):
    """icase: match includes ignoring case (case_insensitive roots, where plan keys are folded)."""
    return None if includes is None else Filter(includes, DENY_PATTERNS, icase)


def scan(root: Path, patterns: list[str], dataless: set[str] | None = None,
         includes: list[str] | None = None, denied: set[str] | None = None, icase: bool = False) -> Manifest:
    """Walk root, return manifest of regular files (symlinks skipped).
    Cloud-only (dataless) files are left out of the manifest and added to `dataless`.
    With `includes`, only matching files are listed, directories no pattern can reach are never
    entered, and files on the deny list are added to `denied` instead of the manifest."""
    out: Manifest = {}
    root = root.expanduser()
    if not root.is_dir():
        return out
    inc = include_filter(includes, icase)
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = os.path.relpath(dirpath, root)
        rel_dir = "" if rel_dir == "." else rel_dir
        keep = []
        for d in dirnames:
            rel = f"{rel_dir}/{d}" if rel_dir else d
            if not excluded(rel, patterns) and not os.path.islink(os.path.join(dirpath, d)) \
                    and (inc is None or inc.descend(rel)):
                keep.append(d)
        dirnames[:] = keep
        for f in filenames:
            rel = f"{rel_dir}/{f}" if rel_dir else f
            if excluded(rel, patterns) or (inc is not None and not inc.wants(rel)):
                continue
            full = os.path.join(dirpath, f)
            try:
                st = os.lstat(full)
            except OSError:
                continue
            if not os.path.isfile(full) or os.path.islink(full):
                continue
            if inc is not None and inc.denied(rel):
                if denied is not None:
                    denied.add(norm(rel))
                continue
            if getattr(st, "st_flags", 0) & SF_DATALESS:
                if dataless is not None:
                    dataless.add(norm(rel))
                continue
            out[norm(rel)] = (int(st.st_mtime), int(st.st_size))
    return out


def parse_lines(text: str, patterns: list[str], dataless: set[str] | None = None,
                includes: list[str] | None = None, denied: set[str] | None = None, icase: bool = False) -> Manifest:
    """Parse 'mtime<TAB>size<TAB>relpath' records (remote lister output).
    Records are NUL-separated (python lister) or newline-separated (stat fallback).
    size == -1 marks a cloud-only file: kept out of the manifest, added to `dataless`.
    With `includes`: size == -2 marks a file the peer's lister denied, and the allowlist and deny
    list are applied again here (to `denied`, never to the manifest)."""
    out: Manifest = {}
    inc = include_filter(includes, icase)
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
        if inc is not None:
            if not inc.wants(rel):
                continue
            if size == "-2" or inc.denied(rel):
                if denied is not None:
                    denied.add(rel)
                continue
        if int(size) < 0:
            if dataless is not None:
                dataless.add(rel)
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


# Remote lister: runs under `python3 - <root> [icase|case include ...]` on the peer; prints
# mtime\tsize\trelpath NUL-terminated (file names may contain newlines). With includes, only matching
# files are listed and denied ones are reported as size -2 (name only).
LISTER_PY = FILTER_PY + "\nDENY = %r\n" % (list(DENY_PATTERNS),) + r'''
import os, sys
root = os.path.expanduser(sys.argv[1])
inc = Filter(sys.argv[3:], DENY, sys.argv[2] == "icase") if len(sys.argv) > 2 else None
out = sys.stdout
for dirpath, dirnames, filenames in os.walk(root):
    dirnames[:] = [d for d in dirnames if not os.path.islink(os.path.join(dirpath, d))]
    if inc is not None:
        rd = os.path.relpath(dirpath, root)
        rd = "" if rd == "." else rd + "/"
        dirnames[:] = [d for d in dirnames if inc.descend(rd + d)]
    for f in filenames:
        full = os.path.join(dirpath, f)
        if inc is not None and not inc.wants(os.path.relpath(full, root)):
            continue
        if os.path.islink(full) or not os.path.isfile(full):
            continue
        try:
            st = os.lstat(full)
        except OSError:
            continue
        rel = os.path.relpath(full, root)
        if inc is not None and inc.denied(rel):
            out.write("0\t-2\t%s\0" % rel)
            continue
        size = -1 if getattr(st, "st_flags", 0) & 0x40000000 else st.st_size
        out.write("%d\t%d\t%s\0" % (int(st.st_mtime), size, rel))
'''

# Fallback when the peer has no python3: BSD stat (macOS), then GNU stat.
LISTER_SH = (
    'cd {root} && ( find . -type f -exec stat -f "%m%t%z%t%N" {{}} + 2>/dev/null '
    '|| find . -type f -exec stat -c "%Y%t%s%t%n" {{}} + )'
)
