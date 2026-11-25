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


def _sum_speed(speeds: list[str]) -> str:
    """'12.34MB/s' + '1.00GB/s' -> '1.01GB/s' (rsync units)."""
    unit = {"B/s": 1, "kB/s": 1e3, "MB/s": 1e6, "GB/s": 1e9}
    total = 0.0
    for sp in speeds:
        m = re.match(r"([\d.]+)(kB/s|MB/s|GB/s|B/s)", sp or "")
        if m:
            total += float(m.group(1)) * unit[m.group(2)]
    if total >= 1e9:
        return f"{total / 1e9:.2f}GB/s"
    if total >= 1e6:
        return f"{total / 1e6:.2f}MB/s"
    return f"{total / 1e3:.2f}kB/s" if total else ""


class Bar:
    """One line, redrawn with \\r. Silent unless stream is a TTY."""

    def __init__(self, label: str, nfiles: int, total_bytes: int, stream=None, enabled: bool | None = None, multi: bool = False):
        self.label, self.nfiles, self.total_bytes, self.multi = label, nfiles, total_bytes, multi
        self.stream = stream or sys.stderr
        self.enabled = self.stream.isatty() if enabled is None else enabled
        self.last_draw = 0.0
        self.width = max(20, min(40, shutil.get_terminal_size((80, 20)).columns - 60))
        self.started = time.monotonic()
        if self.enabled:
            self.draw({"bytes": 0, "pct": 0, "speed": "", "eta": "", "xfr": 0, "left": nfiles, "total": nfiles}, force=True)

    def feed(self, line: str, stream: int = 0) -> None:
        """Progress line from one rsync stream; totals are aggregated across streams."""
        p = parse_progress(line)
        if not p:
            return
        if not hasattr(self, "_streams"):
            self._streams = {}
        self._streams[stream] = p
        if len(self._streams) == 1 and stream == 0 and not self.multi:
            self.draw(p)
            return
        agg = {"bytes": sum(x["bytes"] for x in self._streams.values()), "speed": "", "eta": p.get("eta", ""),
               "xfr": sum((x.get("xfr") or 0) for x in self._streams.values()), "left": None, "total": None}
        agg["pct"] = int(100 * agg["bytes"] / self.total_bytes) if self.total_bytes else p["pct"]
        agg["speed"] = _sum_speed([x.get("speed", "") for x in self._streams.values()])
        self.draw(agg)

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


class MultiDisplay:
    """One terminal line per peer, redrawn together (parallel sync). Thread-safe."""

    def __init__(self, names: list[str], stream=None):
        import threading
        self.stream = stream or sys.stderr
        self.enabled = self.stream.isatty()
        self.names = list(names)
        self.lines = {n: "" for n in names}
        self.lock = threading.Lock()
        self.drawn = 0

    def slot(self, name: str):
        return _SlotStream(self, name)

    def update(self, name: str, text: str) -> None:
        with self.lock:
            self.lines[name] = text
            self._redraw()

    def _redraw(self) -> None:
        if not self.enabled:
            return
        cols = shutil.get_terminal_size((120, 20)).columns - 1
        out = ""
        if self.drawn:
            out += f"\033[{self.drawn}A"
        for n in self.names:
            out += "\r\033[K" + (self.lines[n] or f"   {n:<10} …")[:cols] + "\n"
        self.drawn = len(self.names)
        self.stream.write(out)
        self.stream.flush()

    def close(self) -> None:
        pass


class _SlotStream:
    """File-like target for Bar: collects the last \\r-line and pushes it into the display."""

    def __init__(self, display: MultiDisplay, name: str):
        self.display, self.name, self.buf = display, name, ""

    def isatty(self) -> bool:
        return True

    def write(self, text: str) -> None:
        text = text.replace("\033[K", "")
        if "\r" in text:
            text = text.rsplit("\r", 1)[1]
        self.buf = text.rstrip("\n")

    def flush(self) -> None:
        self.display.update(self.name, f"   {self.name:<10}" + self.buf.lstrip())
