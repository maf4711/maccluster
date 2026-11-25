"""File fidelity: which rsync on each side, and whether xattrs / ACLs / creation times can travel.

macOS ships openrsync (protocol 29, no xattrs) as /usr/bin/rsync; Homebrew's rsync 3.x has
xattrs, ACLs and crtimes. ditto (maccluster) preserved those, so docsync uses them whenever both
sides can, and says so in the report when they cannot.
"""

from __future__ import annotations

import re
import subprocess

from docsync.transport_base import rsync_bin

CANDIDATES = ("/opt/homebrew/bin/rsync", "/usr/local/bin/rsync", "/usr/bin/rsync")
# Kernel-protected or machine-bound attributes: copying them fails (EPERM) or is meaningless.
XATTR_SKIP = ("com.apple.provenance", "com.apple.macl", "com.apple.rootless")
PROBE_SH = ("for r in " + " ".join(CANDIDATES) + '; do [ -x "$r" ] && { echo "PATH $r"; "$r" --version 2>&1 | head -20; '
            'echo END; }; done; true')

_local_cache: set[str] | None = None


def caps_of(version_text: str) -> set[str]:
    """{'xattrs','acls','crtimes'} that an `rsync --version` text advertises (rsync ≥ 3 only)."""
    if not re.search(r"rsync\s+version\s+3\.", version_text):
        return set()
    low = version_text.lower()
    caps = set()
    for word, cap in (("xattrs", "xattrs"), ("acls", "acls"), ("crtimes", "crtimes")):
        if re.search(rf"(?<!no ){word}", low):
            caps.add(cap)
    return caps


def parse_probe(text: str) -> list[tuple[str, set[str]]]:
    """PROBE_SH output → [(rsync path, caps)] in preference order."""
    out: list[tuple[str, set[str]]] = []
    for block in text.split("END"):
        m = re.search(r"^PATH (\S+)", block, re.M)
        if m:
            out.append((m.group(1), caps_of(block)))
    return out


def local_caps() -> set[str]:
    global _local_cache
    if _local_cache is None:
        try:
            _local_cache = caps_of(subprocess.run([rsync_bin(), "--version"], text=True,
                                                  capture_output=True, timeout=10).stdout)
        except (OSError, subprocess.TimeoutExpired):
            _local_cache = set()
    return _local_cache


def flags_for(caps: set[str]) -> list[str]:
    flags: list[str] = []
    if "xattrs" in caps:
        flags.append("-X")
        flags += [f"--filter=-x {name}" for name in XATTR_SKIP]
    if "acls" in caps:
        flags.append("-A")
    if "crtimes" in caps:
        flags.append("--crtimes")
    return flags


def choose(local: set[str], remote: list[tuple[str, set[str]]]) -> tuple[str | None, list[str], str]:
    """(remote rsync path or None, extra flags, warning or ''). Prefers the most capable peer rsync."""
    if not remote:
        return None, [], "peer rsync not found in " + ", ".join(CANDIDATES) + " — synced without xattrs/ACLs"
    path, rcaps = max(remote, key=lambda e: len(e[1]))
    both = local & rcaps
    wanted = {"xattrs", "acls", "crtimes"}
    here, there = sorted(wanted - local), sorted(wanted - rcaps)
    parts = ([f"this Mac's rsync lacks {'/'.join(here)}"] if here else []) + \
            ([f"peer rsync {path} lacks {'/'.join(there)}"] if there else [])
    warn = "; ".join(parts) + " — synced without them" if parts else ""
    return path, flags_for(local & rcaps), warn
