"""Optional action audit port."""

from __future__ import annotations

from typing import Protocol


class AuditPort(Protocol):
    def record(self, action: str, result: str, **fields: str) -> None: ...
