"""Guard the sync_service split: extracted modules exist, re-exports stay identical."""

from __future__ import annotations

import importlib

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
}


@pytest.mark.parametrize(
    ("module", "name"),
    [(m, n) for m, names in _EXPORTS.items() for n in names],
)
def test_extracted_name_is_reexported_identically(module: str, name: str) -> None:
    mod = importlib.import_module(module)
    obj = getattr(mod, name)
    assert getattr(sync_service, name) is obj, f"{name} must be re-exported from sync_service"
