"""Guard the sync_service split: extracted modules exist, re-exports stay identical."""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

from maccluster.services import sync_service

# module -> names that must live there AND be re-exported from sync_service
_EXPORTS: dict[str, tuple[str, ...]] = {
    "maccluster.services.sync_ssh": (
        "_ssh_argv",
        "_scp_argv",
        "_preflight_ssh",
        "_ssh_cat_write_argv",
        "_ssh_cat_read_argv",
        "_scp_one_file",
    ),
    "maccluster.services.sync_inventory": (
        "FileMeta",
        "LocalInventory",
        "_norm_rel",
        "is_excluded",
        "_INV_PREF",
        "_inv_skip_names",
        "_INV_SKIP_NAMES",
        "_UF_DATALESS",
        "_safe_scandir",
        "inventory_local",
        "describe_partial",
        "guard_partial_inventory",
        "parse_inventory_text",
    ),
    "maccluster.services.sync_inventory_remote": (
        "_REMOTE_INVENTORY_PY",
        "_remote_inventory",
    ),
    "maccluster.services.sync_plan": (
        "plan_transfers",
        "apply_batch_limits",
        "classify_compare",
        "DeltaBucket",
        "PreciseDelta",
        "_bucket_from",
        "precise_delta",
        "format_precise_delta",
        "SYNC_CHUNK_BYTES",
        "SYNC_CHUNK_FILES",
        "SYNC_LARGE_FILE_BYTES",
        "_chunk_rels",
        "_bytes_for_rels",
        "_split_large_files",
        "_sample_list",
    ),
    "maccluster.services.sync_pull": (
        "_REMOTE_STAGE_PY",
        "_transfer_large_files_pull",
        "_transfer_pull_once",
        "_transfer_pull",
    ),
    "maccluster.services.sync_push": (
        "_stage_hardlinks",
        "_transfer_large_files_push",
        "_transfer_push_once",
        "_transfer_push",
    ),
    "maccluster.services.sync_prep": (
        "_free_bytes",
        "_remote_free_bytes",
        "_maybe_apfs_snapshot",
        "_notify_fail",
        "_run_force_icloud",
    ),
}


@pytest.mark.parametrize(
    ("module", "name"),
    [(m, n) for m, names in _EXPORTS.items() for n in names],
)
def test_extracted_name_is_reexported_identically(module: str, name: str) -> None:
    mod = importlib.import_module(module)
    obj = getattr(mod, name)
    assert getattr(sync_service, name) is obj, f"{name} must be re-exported from sync_service"


def _line_count(module: str) -> int:
    path = Path(importlib.import_module(module).__file__)
    return len(path.read_text(encoding="utf-8").splitlines())


def test_sync_service_stays_under_1000_lines() -> None:
    # CLAUDE.md: sync_service.py must not grow again; new logic goes into modules.
    assert _line_count("maccluster.services.sync_service") < 1000


@pytest.mark.parametrize("module", sorted(_EXPORTS))
def test_extracted_module_stays_under_500_lines(module: str) -> None:
    assert _line_count(module) < 500
