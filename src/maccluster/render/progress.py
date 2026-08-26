"""Terminal progress bar for sync home (stderr; TTY-aware)."""

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
    if bps <= 0:
        return "— B/s"
    return f"{format_bytes(bps)}/s"


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


def shorten_path(path: str, max_len: int = 42) -> str:
    path = path.replace("\n", " ").strip()
    if len(path) <= max_len:
        return path
    if max_len < 8:
        return path[:max_len]
    head = max_len // 2 - 1
    tail = max_len - head - 1
    return path[:head] + "…" + path[-tail:]


@dataclass
class ProgressState:
    phase: str = ""
    direction: str = ""  # push | pull | plan | inv
    path: str = ""
    file_index: int = 0
    file_total: int = 0
    bytes_done: int = 0
    bytes_total: int = 0
    files_done: int = 0
    files_total: int = 0
    detail: str = ""


@dataclass
class SyncProgress:
    """Progress on stderr: live bar on TTY, periodic lines when piped (unless force)."""

    enabled: bool = True
    stream: TextIO = field(default_factory=lambda: sys.stderr)
    force: bool = False
    bar_width: int = 28
    min_interval_s: float = 0.08
    plain_interval_s: float = 1.0  # non-TTY: emit at most ~1 line/s
    _started: float = field(default_factory=time.monotonic, init=False)
    _last_draw: float = field(default=0.0, init=False)
    _last_bytes: int = field(default=0, init=False)
    _last_rate_t: float = field(default_factory=time.monotonic, init=False)
    _rate_bps: float = field(default=0.0, init=False)
    _state: ProgressState = field(default_factory=ProgressState, init=False)
    _dirty: bool = field(default=False, init=False)
    _finished: bool = field(default=False, init=False)
    _tty: bool = field(default=True, init=False)

    def __post_init__(self) -> None:
        stream = self.stream
        is_tty = hasattr(stream, "isatty") and stream.isatty()
        self._tty = bool(is_tty or self.force)
        if self.enabled and not self.force and not is_tty:
            # Keep enabled for phase notes + occasional percent lines (no \r bar).
            self.min_interval_s = self.plain_interval_s

    def reset_timer(self) -> None:
        self._started = time.monotonic()
        self._last_rate_t = self._started
        self._last_bytes = 0
        self._rate_bps = 0.0

    def set_totals(self, *, files: int = 0, bytes_: int = 0) -> None:
        self._state.files_total = max(0, files)
        self._state.bytes_total = max(0, bytes_)
        self._dirty = True
        self._draw(force=True)

    def phase(self, name: str, *, direction: str = "", detail: str = "") -> None:
        self._state.phase = name
        if direction:
            self._state.direction = direction
        if detail:
            self._state.detail = detail
        self._state.path = ""
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
        force: bool = False,
    ) -> None:
        st = self._state
        if path is not None:
            st.path = path
        if file_index is not None:
            st.file_index = file_index
        if file_total is not None:
            st.file_total = file_total
        if bytes_done is not None:
            st.bytes_done = max(0, bytes_done)
            self._update_rate(st.bytes_done)
        if bytes_total is not None:
            st.bytes_total = max(0, bytes_total)
        if files_done is not None:
            st.files_done = files_done
        if files_total is not None:
            st.files_total = files_total
        if direction is not None:
            st.direction = direction
        if phase is not None:
            st.phase = phase
        if detail is not None:
            st.detail = detail
        self._dirty = True
        self._draw(force=force)

    def _update_rate(self, bytes_done: int) -> None:
        now = time.monotonic()
        dt = now - self._last_rate_t
        if dt >= 0.25:
            db = bytes_done - self._last_bytes
            if db >= 0 and dt > 0:
                instant = db / dt
                # EMA for smoother display
                self._rate_bps = (
                    instant if self._rate_bps <= 0 else (0.35 * instant + 0.65 * self._rate_bps)
                )
            self._last_bytes = bytes_done
            self._last_rate_t = now
        elif bytes_done < self._last_bytes:
            self._last_bytes = bytes_done
            self._last_rate_t = now

    def _pct(self) -> float:
        st = self._state
        if st.bytes_total > 0:
            return 100.0 * min(st.bytes_done, st.bytes_total) / st.bytes_total
        if st.files_total > 0:
            return 100.0 * min(st.files_done, st.files_total) / st.files_total
        if st.file_total > 0 and st.file_index > 0:
            return 100.0 * min(st.file_index, st.file_total) / st.file_total
        return 0.0

    def _draw(self, *, force: bool = False) -> None:
        if not self.enabled or self._finished:
            return
        now = time.monotonic()
        if not force and (now - self._last_draw) < self.min_interval_s:
            return
        if not self._dirty and not force:
            return
        self._last_draw = now
        self._dirty = False

        st = self._state
        pct = self._pct()
        bar = render_bar(pct, width=self.bar_width)
        rate = self._rate_bps
        if st.bytes_total > 0 and rate > 0:
            remain = max(0, st.bytes_total - st.bytes_done)
            eta = format_eta(remain / rate)
        elif st.files_total > 0 and st.files_done > 0:
            elapsed = max(1e-6, now - self._started)
            per = elapsed / st.files_done
            eta = format_eta(per * max(0, st.files_total - st.files_done))
        else:
            eta = format_eta(None)

        dir_tag = f"{st.direction} " if st.direction else ""
        phase = st.phase or "sync"
        if st.bytes_total > 0:
            size_part = f"{format_bytes(st.bytes_done)}/{format_bytes(st.bytes_total)}"
        elif st.files_total > 0:
            size_part = f"{st.files_done}/{st.files_total} files"
        elif st.file_total > 0:
            size_part = f"{st.file_index}/{st.file_total} files"
        else:
            size_part = st.detail or "…"

        what = st.path or st.detail or ""
        what = shorten_path(what, 40) if what else "—"

        body = (
            f"[{bar}] {pct:5.1f}%  {dir_tag}{phase}  "
            f"{size_part}  {format_rate(rate)}  {eta}  {what}"
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

    def phase(self, name: str, *, direction: str = "", detail: str = "") -> None:
        return None

    def update(self, **kwargs) -> None:
        return None

    def finish(self, message: str = "") -> None:
        return None

    def note(self, message: str) -> None:
        return None


ProgressLike = SyncProgress | NullProgress
