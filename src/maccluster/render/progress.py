"""Terminal progress bar for sync home (stderr; TTY-aware).

Always shows download (D) and upload (U) rates so speed is never blank.
"""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass, field
from typing import TextIO


def format_bytes(n: float) -> str:
    n = float(max(0, n))
    for unit, div in (("TB", 1e12), ("GB", 1e9), ("MB", 1e6), ("KB", 1e3)):
        if n >= div:
            return f"{n / div:.2f} {unit}"
    return f"{int(n)} B"


def format_rate(bps: float) -> str:
    """Human rate; always a number (never a dash) so D/U stays readable."""
    if bps <= 0:
        return "0 B/s"
    return f"{format_bytes(bps)}/s"


def format_files_rate(fps: float) -> str:
    if fps <= 0:
        return "0 f/s"
    if fps >= 1000:
        return f"{fps / 1000:.1f}k f/s"
    return f"{fps:.0f} f/s"


def format_eta(seconds: float | None) -> str:
    if seconds is None or seconds < 0 or seconds == float("inf"):
        return "ETA —"
    s = int(seconds)
    if s < 60:
        return f"ETA {s}s"
    m, s = divmod(s, 60)
    if m < 60:
        return f"ETA {m}m{s:02d}s"
    h, m = divmod(m, 60)
    return f"ETA {h}h{m:02d}m"


def render_bar(pct: float, *, width: int = 28) -> str:
    pct = max(0.0, min(100.0, pct))
    filled = int(round(width * pct / 100.0))
    filled = min(width, max(0, filled))
    return "█" * filled + "░" * (width - filled)


def render_indeterminate_bar(elapsed_s: float, *, width: int = 28, block: int = 4) -> str:
    """Bouncing block for a phase whose total is not known yet.

    An inventory does not know how many files it will find, so there is no
    honest percentage to draw. A moving block says "working" without inventing
    one (the old sawtooth read as real progress, then jumped backwards).
    """
    width = max(1, int(width))
    block = max(1, min(int(block), width))
    span = width - block
    if span <= 0:
        return "█" * width
    pos = int(max(0.0, elapsed_s) * 8) % (2 * span)
    if pos > span:
        pos = 2 * span - pos
    return "░" * pos + "█" * block + "░" * (width - block - pos)


def clamp_pct(pct: float | None, *, floor: float = 0.0) -> float | None:
    """Displayed percent: bounded to 0–100 and never below the phase floor.

    The scan discovers work while it runs, so the raw ratio can fall when the
    denominator grows (observed 35.0% → 5.1% mid-inventory). Within one phase
    the number only moves forward; ``None`` stays ``None`` because an unknown
    total must render as indeterminate, not as a made-up figure.
    """
    if pct is None:
        return None
    return max(floor, max(0.0, min(100.0, float(pct))))


def shorten_path(path: str, max_len: int = 42) -> str:
    path = path.replace("\n", " ").strip()
    if len(path) <= max_len:
        return path
    if max_len < 8:
        return path[:max_len]
    head = max_len // 2 - 1
    tail = max_len - head - 1
    return path[:head] + "…" + path[-tail:]


def _ema(prev: float, instant: float, *, alpha: float = 0.35) -> float:
    if prev <= 0:
        return instant
    return alpha * instant + (1.0 - alpha) * prev


@dataclass
class ProgressState:
    phase: str = ""
    direction: str = ""  # push | pull | local | remote | plan
    path: str = ""
    file_index: int = 0
    file_total: int = 0
    bytes_done: int = 0
    bytes_total: int = 0
    files_done: int = 0
    files_total: int = 0
    detail: str = ""
    transport: str = ""  # rdma | tb | wifi — rung currently moving bytes


@dataclass
class SyncProgress:
    """Progress on stderr: live bar on TTY, periodic lines when piped (unless force).

    Rate display is always ``D:<rate> U:<rate>`` (download / upload).
    """

    enabled: bool = True
    stream: TextIO = field(default_factory=lambda: sys.stderr)
    force: bool = False
    bar_width: int = 28
    min_interval_s: float = 0.08
    plain_interval_s: float = 1.0  # non-TTY: emit at most ~1 line/s
    _started: float = field(default_factory=time.monotonic, init=False)
    _last_draw: float = field(default=0.0, init=False)
    _last_rate_t: float = field(default_factory=time.monotonic, init=False)
    # Separate D/U byte counters for transfer phases
    _last_down_bytes: int = field(default=0, init=False)
    _last_up_bytes: int = field(default=0, init=False)
    _down_bps: float = field(default=0.0, init=False)
    _up_bps: float = field(default=0.0, init=False)
    # Inventory / scan rate (files + scanned bytes)
    _last_files: int = field(default=0, init=False)
    _last_scan_bytes: int = field(default=0, init=False)
    _files_fps: float = field(default=0.0, init=False)
    _scan_bps: float = field(default=0.0, init=False)
    _state: ProgressState = field(default_factory=ProgressState, init=False)
    _dirty: bool = field(default=False, init=False)
    _finished: bool = field(default=False, init=False)
    _tty: bool = field(default=True, init=False)
    # High-water mark of the displayed percent, plus the counter it came from.
    # Reset on a phase change or a new counter so the bar never rewinds inside
    # one phase but still starts over for genuinely new work (see clamp_pct).
    _pct_floor: float = field(default=0.0, init=False)
    _pct_source: str = field(default="", init=False)

    def __post_init__(self) -> None:
        stream = self.stream
        is_tty = hasattr(stream, "isatty") and stream.isatty()
        self._tty = bool(is_tty or self.force)
        if self.enabled and not self.force and not is_tty:
            # Keep enabled for phase notes + occasional percent lines (no \r bar).
            self.min_interval_s = self.plain_interval_s

    def reset_timer(self) -> None:
        self._pct_floor = 0.0
        self._pct_source = ""
        self._started = time.monotonic()
        self._last_rate_t = self._started
        self._last_down_bytes = 0
        self._last_up_bytes = 0
        self._down_bps = 0.0
        self._up_bps = 0.0
        self._last_files = 0
        self._last_scan_bytes = 0
        self._files_fps = 0.0
        self._scan_bps = 0.0

    def set_totals(self, *, files: int = 0, bytes_: int = 0) -> None:
        self._state.files_total = max(0, files)
        self._state.bytes_total = max(0, bytes_)
        # New totals are a new unit of work: start the high-water mark over.
        self._pct_floor = 0.0
        self._pct_source = ""
        self._dirty = True
        self._draw(force=True)

    def phase(
        self, name: str, *, direction: str = "", detail: str = "", transport: str = ""
    ) -> None:
        self._state.phase = name
        if direction:
            self._state.direction = direction
        if detail:
            self._state.detail = detail
        if transport:
            self._state.transport = transport
        self._state.path = ""
        # Phase change: percent starts over (and so does the rate sample window,
        # so rates don't spike from stale counters).
        self._pct_floor = 0.0
        self._pct_source = ""
        now = time.monotonic()
        self._last_rate_t = now
        self._last_down_bytes = (
            self._state.bytes_done if direction == "pull" else self._last_down_bytes
        )
        self._last_up_bytes = self._state.bytes_done if direction == "push" else self._last_up_bytes
        if name == "inventory":
            self._last_files = self._state.files_done
            self._last_scan_bytes = self._state.bytes_done
            self._files_fps = 0.0
            self._scan_bps = 0.0
        self._dirty = True
        self._draw(force=True)

    def update(
        self,
        *,
        path: str | None = None,
        file_index: int | None = None,
        file_total: int | None = None,
        bytes_done: int | None = None,
        bytes_total: int | None = None,
        files_done: int | None = None,
        files_total: int | None = None,
        direction: str | None = None,
        phase: str | None = None,
        detail: str | None = None,
        transport: str | None = None,
        force: bool = False,
    ) -> None:
        st = self._state
        if path is not None:
            st.path = path
        if transport is not None:
            st.transport = transport
        if file_index is not None:
            st.file_index = file_index
        if file_total is not None:
            st.file_total = file_total
        if bytes_total is not None:
            st.bytes_total = max(0, bytes_total)
        if files_total is not None:
            st.files_total = files_total
        if direction is not None:
            st.direction = direction
        if phase is not None:
            st.phase = phase
        if detail is not None:
            st.detail = detail

        if files_done is not None:
            st.files_done = max(0, files_done)

        if bytes_done is not None:
            st.bytes_done = max(0, bytes_done)

        self._refresh_rates()
        self._dirty = True
        self._draw(force=force)

    def _traffic_lane(self) -> str:
        """Which rate bucket current phase/direction feeds: down | up | scan."""
        st = self._state
        d = (st.direction or "").lower()
        p = (st.phase or "").lower()
        if d == "pull" or "pull" in p:
            return "down"
        if d == "push" or "push" in p:
            return "up"
        if p == "inventory" or d in ("local", "remote"):
            return "scan"
        # default: treat as upload when transferring without explicit direction
        if p in ("transfer", "stage", "copy"):
            return "up"
        return "scan"

    def _refresh_rates(self) -> None:
        """Update D/U/scan rates from current counters (EMA, ≥0.2s window)."""
        now = time.monotonic()
        dt = now - self._last_rate_t
        if dt < 0.2:
            return
        st = self._state
        lane = self._traffic_lane()

        if lane == "down":
            db = st.bytes_done - self._last_down_bytes
            if db >= 0 and dt > 0:
                self._down_bps = _ema(self._down_bps, db / dt)
            self._last_down_bytes = st.bytes_done
            # Decay idle opposite lane slowly so it does not stick forever
            self._up_bps *= 0.85 if self._up_bps > 0 else 0.0
        elif lane == "up":
            db = st.bytes_done - self._last_up_bytes
            if db >= 0 and dt > 0:
                self._up_bps = _ema(self._up_bps, db / dt)
            self._last_up_bytes = st.bytes_done
            self._down_bps *= 0.85 if self._down_bps > 0 else 0.0
        else:
            # Inventory / scan: track files/s and scanned bytes/s; D/U stay 0
            df = st.files_done - self._last_files
            if df >= 0 and dt > 0:
                self._files_fps = _ema(self._files_fps, df / dt)
            self._last_files = st.files_done
            sb = st.bytes_done - self._last_scan_bytes
            if sb >= 0 and dt > 0:
                self._scan_bps = _ema(self._scan_bps, sb / dt)
            self._last_scan_bytes = st.bytes_done
            # No transfer: show D/U as idle (0)
            self._down_bps = 0.0
            self._up_bps = 0.0

        self._last_rate_t = now

    def _pct(self) -> tuple[float | None, str]:
        """Raw completion plus which counter produced it.

        The caller only holds the no-rewind floor while the *same* counter keeps
        answering: a phase that switches from files to bytes is measuring
        something else, so its percent legitimately starts over.
        """
        st = self._state
        if st.bytes_total > 0:
            return 100.0 * min(st.bytes_done, st.bytes_total) / st.bytes_total, "bytes"
        if st.files_total > 0:
            return 100.0 * min(st.files_done, st.files_total) / st.files_total, "files"
        if st.file_total > 0 and st.file_index > 0:
            return 100.0 * min(st.file_index, st.file_total) / st.file_total, "index"
        # Inventory has no denominator until the walk ends — say so instead of
        # pulsing a fake number that then falls back (35.0% → 5.1%).
        return None, ""

    def _rate_part(self) -> str:
        """Always show D/U; during inventory also show scan speed."""
        st = self._state
        d_u = f"D:{format_rate(self._down_bps)} U:{format_rate(self._up_bps)}"
        lane = self._traffic_lane()
        if lane == "scan" or st.phase == "inventory":
            # Prefer scan B/s when we have sizes; always show files/s too
            scan = format_rate(self._scan_bps) if self._scan_bps > 0 else format_rate(0)
            fps = format_files_rate(self._files_fps)
            return f"{d_u} scan:{scan} {fps}"
        return d_u

    def _draw(self, *, force: bool = False) -> None:
        if not self.enabled or self._finished:
            return
        now = time.monotonic()
        if not force and (now - self._last_draw) < self.min_interval_s:
            return
        if not self._dirty and not force:
            return
        # Keep rates fresh even if only files tick without new bytes
        self._refresh_rates()
        self._last_draw = now
        self._dirty = False

        st = self._state
        raw, pct_source = self._pct()
        if pct_source != self._pct_source:
            self._pct_source = pct_source
            self._pct_floor = 0.0
        pct = clamp_pct(raw, floor=self._pct_floor)
        if pct is None:
            bar = render_indeterminate_bar(now - self._started, width=self.bar_width)
            pct_part = f"{'--':>5}%"
        else:
            self._pct_floor = pct
            bar = render_bar(pct, width=self.bar_width)
            pct_part = f"{pct:5.1f}%"

        active = self._down_bps if self._traffic_lane() == "down" else self._up_bps
        if self._traffic_lane() == "scan":
            active = self._scan_bps
        if st.bytes_total > 0 and active > 0:
            remain = max(0, st.bytes_total - st.bytes_done)
            eta = format_eta(remain / active)
        elif st.files_total > 0 and st.files_done > 0:
            elapsed = max(1e-6, now - self._started)
            per = elapsed / st.files_done
            eta = format_eta(per * max(0, st.files_total - st.files_done))
        elif st.phase == "inventory" and self._files_fps > 0:
            # No known total — show time running
            eta = f"t+{int(now - self._started)}s"
        else:
            eta = format_eta(None)

        dir_tag = f"{st.direction} " if st.direction else ""
        phase = (st.phase or "sync") + (f" transport={st.transport}" if st.transport else "")
        if st.bytes_total > 0:
            size_part = f"{format_bytes(st.bytes_done)}/{format_bytes(st.bytes_total)}"
        elif st.files_total > 0:
            size_part = f"{st.files_done}/{st.files_total} files"
        elif st.file_total > 0:
            size_part = f"{st.file_index}/{st.file_total} files"
        elif st.files_done > 0:
            # Indeterminate inventory: count climbs without a known total
            if st.bytes_done > 0:
                size_part = f"{st.files_done} files {format_bytes(st.bytes_done)}"
            else:
                size_part = f"{st.files_done} files"
        else:
            size_part = st.detail or "…"

        what = st.path or st.detail or ""
        what = shorten_path(what, 36) if what else "—"

        body = (
            f"[{bar}] {pct_part}  {dir_tag}{phase}  {size_part}  {self._rate_part()}  {eta}  {what}"
        )
        try:
            if self._tty:
                self.stream.write("\r\033[2K" + body)
            else:
                self.stream.write(body + "\n")
            self.stream.flush()
        except Exception:
            self.enabled = False

    def finish(self, message: str = "") -> None:
        if not self.enabled:
            self._finished = True
            return
        if not self._finished:
            try:
                if self._tty:
                    self.stream.write("\r\033[2K")
                if message:
                    self.stream.write(message.rstrip() + "\n")
                self.stream.flush()
            except Exception:
                pass
        self._finished = True

    def note(self, message: str) -> None:
        """Print a full line above the bar (clears current bar line first)."""
        if not self.enabled:
            return
        try:
            if self._tty:
                self.stream.write("\r\033[2K" + message.rstrip() + "\n")
            else:
                self.stream.write(message.rstrip() + "\n")
            self.stream.flush()
            self._dirty = True
            # Don't redraw bar immediately after note on plain mode
            if self._tty:
                self._draw(force=True)
        except Exception:
            self.enabled = False


class NullProgress:
    """No-op progress for JSON mode / tests."""

    enabled = False

    def reset_timer(self) -> None:
        return None

    def set_totals(self, *, files: int = 0, bytes_: int = 0) -> None:
        return None

    def phase(
        self, name: str, *, direction: str = "", detail: str = "", transport: str = ""
    ) -> None:
        return None

    def update(self, **kwargs) -> None:
        return None

    def finish(self, message: str = "") -> None:
        return None

    def note(self, message: str) -> None:
        return None


ProgressLike = SyncProgress | NullProgress
