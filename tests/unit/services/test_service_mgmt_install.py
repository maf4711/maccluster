"""Service management with fake."""

from __future__ import annotations

from maccluster.services import service_mgmt


def test_install_status_uninstall(fake_ctx, monkeypatch, tmp_path):
    prog = tmp_path / "maccluster"
    prog.write_text("#!/bin/sh\n")
    prog.chmod(0o755)
    monkeypatch.setattr(service_mgmt, "resolve_program", lambda: prog)
    st = service_mgmt.install_service(fake_ctx)
    assert st.installed
    st2 = service_mgmt.service_status(fake_ctx)
    assert st2.installed
    st3 = service_mgmt.uninstall_service(fake_ctx)
    assert not st3.installed
