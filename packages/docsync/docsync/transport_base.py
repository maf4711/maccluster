"""Shared helpers without dependencies on other docsync modules."""

from __future__ import annotations

import os
import shutil


def rsync_bin() -> str:
    for cand in ("/opt/homebrew/bin/rsync", "/usr/local/bin/rsync"):
        if os.access(cand, os.X_OK):
            return cand
    return shutil.which("rsync") or "rsync"
