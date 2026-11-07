"""Terminal progress bar fed by rsync --info=progress2 lines."""

from __future__ import annotations

import re
import shutil
import sys
import time

from docsync.report import fmt_bytes

# "  1,234,567  45%   12.34MB/s    0:00:12 (xfr#3, to-chk=100/370)"
_LINE = re.compile(r"^\s*([\d,]+)\s+(\d+)%\s+(\S+)\s+(\S+)(?:\s+\(xfr#(\d+),\s+(?:to-chk|ir-chk)=(\d+)/(\d+)\))?")


def parse_progress(line: str) -> dict | None:
    m = _LINE.match(line)
    if not m:
        return None
    done, pct, speed, eta, xfr, left, total = m.groups()
    return {
        "bytes": int(done.replace(",", "")), "pct": int(pct), "speed": speed, "eta": eta,
        "xfr": int(xfr) if xfr else None, "left": int(left) if left else None, "total": int(total) if total else None,
    }


class Bar:
    """One line, redrawn with \\r. Silent unless stream is a TTY."""

    def __init__(self, label: str, nfiles: int, total_bytes: int, stream=None, enabled: bool | None = None):
        self.label, self.nfiles, self.total_bytes = label, nfiles, total_bytes
        self.stream = stream or sys.stderr
        self.enabled = self.stream.isatty() if enabled is None else enabled
        self.last_draw = 0.0
        self.width = max(20, min(40, shutil.get_terminal_size((80, 20)).columns - 60))
        self.started = time.monotonic()
        if self.enabled:
            self.draw({"bytes": 0, "pct": 0, "speed": "", "eta": "", "xfr": 0, "left": nfiles, "total": nfiles}, force=True)

    def feed(self, line: str) -> None:
        p = parse_progress(line)
        if p:
            self.draw(p)

    def draw(self, p: dict, force: bool = False) -> None:
        if not self.enabled:
            return
        now = time.monotonic()
        if not force and now - self.last_draw < 0.1:
            return
        self.last_draw = now
        pct = max(0, min(100, p["pct"]))
        filled = int(self.width * pct / 100)
        bar = "█" * filled + "░" * (self.width - filled)
        done_files = (p["total"] - p["left"]) if p.get("total") is not None and p.get("left") is not None else (p.get("xfr") or 0)
        files = f"{done_files:,}/{self.nfiles:,} files"
        kb = f"{p['bytes'] // 1024:,}/{self.total_bytes // 1024:,} KB" if self.total_bytes else f"{p['bytes'] // 1024:,} KB"
        eta = f"ETA {p['eta']}" if p.get("eta") and p["eta"] not in ("0:00:00", "") else ""
        line = f"\r   {self.label:<7} [{bar}] {pct:3d}%  {kb}  {p.get('speed') or '-':>10}  {files}  {eta}"
        self.stream.write(line[: shutil.get_terminal_size((120, 20)).columns - 1].ljust(0) + "\033[K")
        self.stream.flush()

    def finish(self, nbytes: int, nfiles: int) -> None:
        if not self.enabled:
            return
        secs = time.monotonic() - self.started
        rate = fmt_bytes(int(nbytes / secs)) + "/s" if secs > 0 else "-"
        self.stream.write(f"\r   {self.label:<7} [{'█' * self.width}] 100%  {nbytes // 1024:,} KB  {rate:>10}  {nfiles:,} files  {secs:.1f}s\033[K\n")
        self.stream.flush()
