"""Fabric busy guard — env + file, no remote HTTP."""

from __future__ import annotations

import pytest

from maccluster.errors import CliError
from maccluster.services.busy_guard import read_busy_state


def test_env_truthy_wins_without_file(tmp_path):
    path = tmp_path / "busy"
    st = read_busy_state(env={"MACCLUSTER_BUSY": "YES"}, busy_path=path)
    assert st.busy is True
    assert "MACCLUSTER_BUSY" in st.reason


def test_env_falsey_then_absent_file_is_idle(tmp_path):
    path = tmp_path / "busy"
    st = read_busy_state(env={"MACCLUSTER_BUSY": "0"}, busy_path=path)
    assert st.busy is False
    assert st.reason == ""


def test_busy_file_first_line_is_reason(tmp_path):
    path = tmp_path / "busy"
    path.write_text("live trading window\nsecond line\n", encoding="utf-8")
    st = read_busy_state(env={}, busy_path=path)
    assert st.busy is True
    assert st.reason == "live trading window"


def test_empty_busy_file_still_busy(tmp_path):
    path = tmp_path / "busy"
    path.write_text("", encoding="utf-8")
    st = read_busy_state(env={}, busy_path=path)
    assert st.busy is True
    assert "busy" in st.reason.lower()


def test_symlink_busy_file_refused(tmp_path):
    target = tmp_path / "real"
    target.write_text("secret", encoding="utf-8")
    path = tmp_path / "busy"
    path.symlink_to(target)
    with pytest.raises(CliError) as ei:
        read_busy_state(env={}, busy_path=path)
    assert ei.value.exit_code == 2
    assert "symlink" in ei.value.message.lower()
