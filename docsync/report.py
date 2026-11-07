"""Sync report: files, bytes, time, throughput — per peer and in total."""

from __future__ import annotations

import time
from dataclasses import dataclass, field


def fmt_bytes(n: int) -> str:
    kb = n / 1024
    if kb < 1024:
        return f"{kb:,.1f} KB"
    mb = kb / 1024
    if mb < 1024:
        return f"{mb:,.1f} MB"
    return f"{mb / 1024:,.2f} GB"


def fmt_secs(s: float) -> str:
    if s < 60:
        return f"{s:.1f}s"
    m, sec = divmod(int(s), 60)
    if m < 60:
        return f"{m}m {sec:02d}s"
    h, m = divmod(m, 60)
    return f"{h}h {m:02d}m {sec:02d}s"


def fmt_rate(nbytes: int, secs: float) -> str:
    if secs <= 0:
        return "-"
    return f"{fmt_bytes(int(nbytes / secs))}/s"


@dataclass
class Phase:
    name: str
    secs: float = 0.0


@dataclass
class PeerReport:
    peer: str
    host: str = ""
    ok: bool = True
    error: str = ""
    dry: bool = False
    started: float = field(default_factory=time.monotonic)
    secs: float = 0.0
    phases: list[Phase] = field(default_factory=list)
    local_files: int = 0
    local_bytes: int = 0
    remote_files: int = 0
    remote_bytes: int = 0
    first_sync: bool = False
    pushed_files: int = 0
    pushed_bytes: int = 0
    pulled_files: int = 0
    pulled_bytes: int = 0
    wire_sent: int = 0
    wire_received: int = 0
    deleted_local: int = 0
    deleted_remote: int = 0
    conflicts: list[tuple[str, str, str]] = field(default_factory=list)  # path, winner, conflict copy
    unchanged: int = 0

    def phase(self, name: str, t0: float) -> None:
        self.phases.append(Phase(name, time.monotonic() - t0))

    def finish(self) -> None:
        self.secs = time.monotonic() - self.started

    @property
    def transferred_bytes(self) -> int:
        return self.pushed_bytes + self.pulled_bytes

    @property
    def transferred_files(self) -> int:
        return self.pushed_files + self.pulled_files

    def render(self) -> str:
        tag = " (DRY RUN — nothing written)" if self.dry else ""
        head = f"── {self.peer} @ {self.host or '?'}{tag}"
        if not self.ok:
            return f"{head}\n   FAILED after {fmt_secs(self.secs)}: {self.error}"
        rows = [
            ("local tree", f"{self.local_files:,} files  {fmt_bytes(self.local_bytes)}"),
            ("remote tree", f"{self.remote_files:,} files  {fmt_bytes(self.remote_bytes)}"),
            ("pushed →", f"{self.pushed_files:,} files  {fmt_bytes(self.pushed_bytes)}"),
            ("pulled ←", f"{self.pulled_files:,} files  {fmt_bytes(self.pulled_bytes)}"),
            ("deleted", f"local {self.deleted_local:,}  remote {self.deleted_remote:,}"),
            ("conflicts", f"{len(self.conflicts):,}"),
            ("unchanged", f"{self.unchanged:,} files"),
            ("on the wire", f"sent {fmt_bytes(self.wire_sent)}  received {fmt_bytes(self.wire_received)}"),
            ("time", f"{fmt_secs(self.secs)}  ({', '.join(f'{p.name} {fmt_secs(p.secs)}' for p in self.phases)})"),
            ("throughput", "n/a (dry run)" if self.dry else fmt_rate(self.transferred_bytes, self.secs)),
        ]
        if self.first_sync:
            rows.insert(2, ("mode", "first sync — union of both trees, newest wins, no deletions"))
        out = [head] + [f"   {k:<12} {v}" for k, v in rows]
        for path, winner, copy in self.conflicts:
            out.append(f"   ⚠ conflict  {path}  → kept {winner}; other side saved as {copy}")
        return "\n".join(out)


@dataclass
class RunReport:
    peers: list[PeerReport] = field(default_factory=list)
    started: float = field(default_factory=time.monotonic)

    def render(self) -> str:
        secs = time.monotonic() - self.started
        files = sum(p.transferred_files for p in self.peers)
        nbytes = sum(p.transferred_bytes for p in self.peers)
        dels = sum(p.deleted_local + p.deleted_remote for p in self.peers)
        confl = sum(len(p.conflicts) for p in self.peers)
        failed = [p.peer for p in self.peers if not p.ok]
        dry = any(p.dry for p in self.peers)
        verb = "planned" if dry else "transferred"
        lines = [p.render() for p in self.peers]
        lines.append("══ TOTAL" + (" (DRY RUN)" if dry else ""))
        lines.append(f"   peers        {len(self.peers) - len(failed)} ok, {len(failed)} failed"
                     + (f" ({', '.join(failed)})" if failed else ""))
        lines.append(f"   files        {files:,} {verb}  ({fmt_bytes(nbytes)}, {nbytes // 1024:,} KB)")
        lines.append(f"   deleted      {dels:,}    conflicts {confl:,}")
        lines.append(f"   time         {fmt_secs(secs)}    throughput "
                     + ("n/a (dry run)" if dry else fmt_rate(nbytes, secs)))
        return "\n".join(lines)

    @property
    def ok(self) -> bool:
        return all(p.ok for p in self.peers)
