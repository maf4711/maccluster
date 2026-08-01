"""Optional action audit log with size rotation."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

from maccluster.config.paths import default_audit_log_path
from maccluster.constants import AUDIT_MAX_BYTES


class AuditLog:
    def __init__(
        self,
        path: Path | None = None,
        *,
        enabled: bool = False,
        max_bytes: int = AUDIT_MAX_BYTES,
    ) -> None:
        self.path = path or default_audit_log_path()
        self.enabled = enabled
        self.max_bytes = max_bytes

    def record(self, action: str, result: str, **fields: str) -> None:
        if not self.enabled:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._rotate_if_needed()
        ts = datetime.now(UTC).isoformat()
        extra = " ".join(f"{k}={_safe(v)}" for k, v in fields.items())
        line = f"{ts} action={_safe(action)} result={_safe(result)} {extra}\n"
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(line)

    def _rotate_if_needed(self) -> None:
        if not self.path.exists():
            return
        try:
            if self.path.stat().st_size < self.max_bytes:
                return
            bak = self.path.with_suffix(self.path.suffix + ".1")
            if bak.exists():
                bak.unlink()
            os.replace(self.path, bak)
        except OSError:
            pass


def _safe(value: str) -> str:
    return value.replace("\n", " ").replace("\r", " ")[:200]


class NullAudit:
    def record(self, action: str, result: str, **fields: str) -> None:
        return
