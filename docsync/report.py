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
    via: str = ""  # Thunderbolt | LAN | LAN (Bonjour) | Thunderbolt (ARP)
    ok: bool = True
    busy: bool = False  # a root lock of this pair was held by another run: skipped, not a failure
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
    cloud_only_local: int = 0
    cloud_only_remote: int = 0
    skipped: int = 0
    deletes_skipped: int = 0
    retries: int = 0
    streams: int = 1
    root: str = ""
    safetynet_files: int = 0
    safetynet_bytes: int = 0
    warnings: list[str] = field(default_factory=list)

    def phase(self, name: str, t0: float) -> None:
        self.phases.append(Phase(name, time.monotonic() - t0))

    def to_dict(self) -> dict:
        keys = ("peer", "root", "host", "via", "ok", "busy", "error", "dry", "secs", "first_sync", "local_files", "local_bytes",
                "remote_files", "remote_bytes", "pushed_files", "pushed_bytes", "pulled_files", "pulled_bytes",
                "wire_sent", "wire_received", "deleted_local", "deleted_remote", "unchanged", "cloud_only_local",
                "cloud_only_remote", "skipped", "deletes_skipped", "retries", "streams", "safetynet_files",
                "safetynet_bytes", "warnings")
        d = {k: getattr(self, k) for k in keys}
        d["warnings"] = list(self.warnings)
        d["phases"] = [{"name": p.name, "secs": round(p.secs, 3)} for p in self.phases]
        d["conflicts"] = [{"path": p, "winner": w, "copy": c} for p, w, c in self.conflicts]
        d["secs"] = round(self.secs, 3)
        return d

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
        head = f"── {self.peer}{f' [{self.root}]' if self.root else ''} @ {self.host or '?'}{' via ' + self.via if self.via else ''}{tag}"
        if self.busy:
            return f"{head}\n   {self.error}"
        if not self.ok:
            return f"{head}\n   FAILED after {fmt_secs(self.secs)}: {self.error}"
        rows = [
            ("local tree", f"{self.local_files:,} files  {fmt_bytes(self.local_bytes)}"),
            ("remote tree", f"{self.remote_files:,} files  {fmt_bytes(self.remote_bytes)}"),
            ("pushed →", f"{self.pushed_files:,} files  {fmt_bytes(self.pushed_bytes)}"),
            ("pulled ←", f"{self.pulled_files:,} files  {fmt_bytes(self.pulled_bytes)}"),
            ("deleted", f"local {self.deleted_local:,}  remote {self.deleted_remote:,}"
                        + (f"  (⚠ {self.deletes_skipped:,} held back by deletion guard)" if self.deletes_skipped else "")),
            ("conflicts", f"{len(self.conflicts):,}"),
            ("unchanged", f"{self.unchanged:,} files"),
            ("cloud-only", f"local {self.cloud_only_local:,}  remote {self.cloud_only_remote:,}  (iCloud-evicted, not touched)"),
            ("skipped", f"{self.skipped:,} (vanished during transfer)"),
            *([("retries", f"{self.retries:,} transport retries")] if self.retries else []),
            *([("streams", f"{self.streams} parallel rsync streams")] if self.streams > 1 else []),
            *([("safetynet", f"{self.safetynet_files:,} files  {fmt_bytes(self.safetynet_bytes)} backed up before overwrite")]
              if self.safetynet_files else []),
            ("on the wire", f"sent {fmt_bytes(self.wire_sent)}  received {fmt_bytes(self.wire_received)}"),
            ("time", f"{fmt_secs(self.secs)}  ({', '.join(f'{p.name} {fmt_secs(p.secs)}' for p in self.phases)})"),
            ("throughput", "n/a (dry run)" if self.dry else fmt_rate(self.transferred_bytes, self.secs)),
        ]
        if self.first_sync:
            rows.insert(2, ("mode", "first sync — union of both trees, newest wins, no deletions"))
        out = [head] + [f"   {k:<12} {v}" for k, v in rows]
        for path, winner, copy in self.conflicts:
            out.append(f"   ⚠ conflict  {path}  → kept {winner}; other side saved as {copy}")
        for w in self.warnings[:10]:
            out.append(f"   ⚠ {w}")
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
        busy = [p.peer for p in self.peers if p.busy]
        dry = any(p.dry for p in self.peers)
        verb = "planned" if dry else "transferred"
        lines = [p.render() for p in self.peers]
        lines.append("══ TOTAL" + (" (DRY RUN)" if dry else ""))
        lines.append(f"   peers        {len(self.peers) - len(failed) - len(busy)} ok, {len(failed)} failed"
                     + (f" ({', '.join(failed)})" if failed else "")
                     + (f", {len(busy)} busy ({', '.join(busy)})" if busy else ""))
        lines.append(f"   files        {files:,} {verb}  ({fmt_bytes(nbytes)}, {nbytes // 1024:,} KB)")
        lines.append(f"   deleted      {dels:,}    conflicts {confl:,}")
        lines.append(f"   time         {fmt_secs(secs)}    throughput "
                     + ("n/a (dry run)" if dry else fmt_rate(nbytes, secs)))
        return "\n".join(lines)

    @property
    def ok(self) -> bool:
        return all(p.ok for p in self.peers)

    @property
    def all_busy(self) -> bool:
        return bool(self.peers) and all(p.busy for p in self.peers)

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "secs": round(time.monotonic() - self.started, 3),
            "transferred_files": sum(p.transferred_files for p in self.peers),
            "transferred_bytes": sum(p.transferred_bytes for p in self.peers),
            "peers": [p.to_dict() for p in self.peers],
        }

    def notable(self) -> str:
        """One line for a notification: failures, conflicts, held-back deletions. Empty if boring."""
        bits = []
        failed = [p.peer for p in self.peers if not p.ok]
        if failed:
            bits.append(f"failed: {', '.join(failed)}")
        confl = sum(len(p.conflicts) for p in self.peers)
        if confl:
            bits.append(f"{confl} conflict{'s' if confl != 1 else ''}")
        held = sum(p.deletes_skipped for p in self.peers)
        if held:
            bits.append(f"{held} deletions held back")
        return "; ".join(bits)
