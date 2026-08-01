"""Audit log rotation."""

from __future__ import annotations

from pathlib import Path

from maccluster.audit.log import AuditLog


def test_disabled_noop(tmp_path: Path):
    log = AuditLog(path=tmp_path / "a.log", enabled=False)
    log.record("up", "ok")
    assert not (tmp_path / "a.log").exists()


def test_write_and_rotate(tmp_path: Path):
    path = tmp_path / "a.log"
    log = AuditLog(path=path, enabled=True, max_bytes=50)
    for i in range(20):
        log.record("heal", "ok", n=str(i))
    assert path.exists() or (tmp_path / "a.log.1").exists()
