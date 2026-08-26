"""Unit tests for iCloud dataless helpers."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from maccluster.services.icloud_materialize import (
    UF_DATALESS,
    is_dataless_stat,
    materialize_tree,
)


def test_is_dataless_stat_flag():
    st = SimpleNamespace(st_flags=UF_DATALESS)
    assert is_dataless_stat(st) is True
    st2 = SimpleNamespace(st_flags=0)
    assert is_dataless_stat(st2) is False
    st3 = SimpleNamespace()  # no st_flags
    assert is_dataless_stat(st3) is False


def test_materialize_tree_missing(tmp_path: Path):
    r = materialize_tree(tmp_path / "nope")
    assert r.scanned == 0
    assert any("missing" in n for n in r.notes)


def test_materialize_tree_no_dataless(tmp_path: Path):
    f = tmp_path / "a.txt"
    f.write_text("hello")
    r = materialize_tree(tmp_path, max_seconds=5)
    assert r.dataless_found == 0
    assert r.materialized == 0
    assert r.remaining_dataless == 0


def test_inventory_skips_dataless_constant_matches_darwin():
    # Documented Darwin value
    assert UF_DATALESS == 0x40000000
    # is_dataless_stat treats missing/None flags as not dataless
    assert is_dataless_stat(SimpleNamespace(st_flags=None)) is False
    assert is_dataless_stat(SimpleNamespace(st_flags=0)) is False
