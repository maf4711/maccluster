"""`docsync advise`: feed the last report, conflicts and warnings to the on-device Apple
Foundation Model (Swift helper, compiled on first use) — with a rule-based fallback."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

from docsync.config import STATE_DIR

SWIFT_SRC = Path(__file__).resolve().parent / "resources" / "docsync-advisor.swift"
HELPER = STATE_DIR / "bin" / "docsync-advisor"


def last_run_block(history: str) -> str:
    """The most recent '=== <timestamp>' block of history.log that holds a report."""
    blocks = re.split(r"^=== ", history, flags=re.M)
    for b in reversed(blocks):
        if "TOTAL" in b:
            return "=== " + b.strip()
    return ""


def original_of(conflict: str) -> str:
    return re.sub(r"\.conflict-[^.]+", "", conflict)


def conflict_facts(conflicts: list[str], tree: dict[str, tuple[int, int]]) -> list[str]:
    """One line per conflict copy and its original: path, size, mtime — the facts the advice rests on."""
    import datetime as dt
    seen: list[str] = []
    for c in conflicts:
        for p in (original_of(c), c):
            if p in seen:
                continue
            seen.append(p)
            e = tree.get(p)
            if e:
                when = dt.datetime.fromtimestamp(e[0]).strftime("%Y-%m-%d %H:%M")
                seen_line = f"- {p}  ({e[1]:,} Bytes, geändert {when})"
            else:
                seen_line = f"- {p}  (nicht vorhanden)"
            yield seen_line


def build_prompt(report: str, conflicts: list[str], warnings: list[str], cloud_only: tuple[int, int] | None,
                 peer: str | None = None, tree: dict[str, tuple[int, int]] | None = None) -> str:
    parts = ["## Letzter docsync-Lauf" + (f" (Peer {peer})" if peer else ""), report or "(kein Report in history.log)"]
    parts.append("## Konfliktkopien in ~/Documents (mit Original, Größe, Änderungszeit)")
    parts += list(conflict_facts(conflicts, tree or {})) or ["- keine"]
    parts.append("## Warnungen")
    parts += [f"- {w}" for w in warnings] or ["- keine"]
    if cloud_only:
        parts.append(f"## iCloud: {cloud_only[0]:,} lokale Dateien sind cloud-only (evicted), {cloud_only[1]:,} echte lokale Dateien")
    parts.append("## Aufgabe\nGib bis zu 8 Empfehlungen: welche Konfliktkopie behalten (nach Größe/Datum), "
                 "ob zurückgehaltene Löschungen wie eine Umbenennung/Groß-Kleinschreibung aussehen, "
                 "und ob ein Peer zuerst über Thunderbolt synchronisiert werden sollte.")
    return "\n".join(parts)


def rule_based(conflicts: list[str], warnings: list[str], sizes: dict[str, int] | None = None) -> list[str]:
    """Deterministic advice when the model is not available (same inputs). Judges by size only."""
    out: list[str] = []
    sizes = sizes or {}
    groups: dict[str, list[str]] = {}
    for c in conflicts:
        groups.setdefault(original_of(c), []).append(c)
    for orig, copies in groups.items():
        versions = [p for p in [orig, *copies] if p in sizes]
        if len(versions) >= 2:
            biggest = max(versions, key=lambda p: sizes[p])
            others = [p for p in versions if p != biggest]
            small = [p for p in others if sizes[p] < 0.9 * sizes[biggest]]
            out.append(f"• {os.path.basename(orig)}: {len(versions)} Versionen — größte ist {os.path.basename(biggest)} "
                       f"({sizes[biggest]:,} Bytes), behalten. "
                       + (f"Deutlich kleiner und damit wahrscheinlich abgeschnitten: "
                          f"{', '.join(os.path.basename(p) + ' (' + format(sizes[p], ',') + ' Bytes)' for p in small)} → löschen."
                          if small else "Die anderen erst nach Sichtung löschen."))
        else:
            out.append(f"• {copies[0]}: Konfliktkopie sichten, Verlierer löschen; die Löschung wird beim nächsten Sync übernommen.")
        if any(c.count(".conflict-") >= 2 for c in copies):
            out.append(f"• {os.path.basename(orig)}: eine Kopie hat zweimal '.conflict-' — entstand bei einem abgebrochenen Lauf; "
                       "nach Größe entscheiden, nicht nach Name.")
    for w in warnings:
        if "deletion guard" in w:
            out.append("• Löschbremse hat Löschungen zurückgehalten: mit `docsync plan -v` prüfen. Tauchen dieselben Namen als Pull in anderer "
                       "Schreibweise auf, ist es eine Umbenennung/Groß-Kleinschreibung, kein echter Löschwunsch. Sonst `docsync sync --force-deletes`.")
        elif "FAILED" in w or "failed" in w:
            out.append(f"• Fehler im letzten Lauf: {w[:120]} → `docsync doctor`, dann erneut `docsync sync`.")
    if not out:
        out.append("• Keine Konflikte, keine zurückgehaltenen Löschungen: nichts zu tun. `docsync tb` zeigt, ob Thunderbolt genutzt wird.")
    return out[:8]


def helper_ready() -> tuple[bool, str]:
    """Compile the Swift helper if missing or stale. Returns (ok, reason-if-not)."""
    if not SWIFT_SRC.exists():
        return False, f"helper source missing: {SWIFT_SRC}"
    swiftc = shutil.which("swiftc")
    if HELPER.exists() and HELPER.stat().st_mtime >= SWIFT_SRC.stat().st_mtime:
        return True, ""
    if not swiftc:
        return False, "swiftc not found (install Xcode Command Line Tools)"
    HELPER.parent.mkdir(parents=True, exist_ok=True)
    res = subprocess.run([swiftc, "-O", "-parse-as-library", "-o", str(HELPER), str(SWIFT_SRC)],
                         text=True, capture_output=True, timeout=600)
    if res.returncode != 0:
        return False, f"swiftc failed: {res.stderr.strip()[-300:]}"
    return True, ""


def availability() -> tuple[bool, str]:
    ok, why = helper_ready()
    if not ok:
        return False, why
    res = subprocess.run([str(HELPER), "--check"], text=True, capture_output=True, timeout=60)
    return res.returncode == 0, res.stdout.strip() or res.stderr.strip()


def ask(prompt: str) -> tuple[int, str]:
    """(exit code, text). 0 ok, 1 unavailable, 2 timeout/error."""
    try:
        res = subprocess.run([str(HELPER)], input=prompt, text=True, capture_output=True, timeout=45)
    except subprocess.TimeoutExpired:
        return 2, "timeout"
    return res.returncode, (res.stdout.strip() if res.returncode == 0 else res.stderr.strip())
