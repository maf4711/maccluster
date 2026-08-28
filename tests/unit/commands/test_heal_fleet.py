"""CLI: heal --fleet flags and incompatibilities."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from maccluster.cli.exit_codes import USAGE
from maccluster.cli.parser import build_parser
from maccluster.commands import heal
from maccluster.errors import CliError


def test_parse_fleet_flags():
    p = build_parser()
    args = p.parse_args(["heal", "--fleet", "--together", "--dry-run", "--peer", "node-b"])
    assert args.fleet is True
    assert args.together is True
    assert args.dry_run is True
    assert args.peer == "node-b"


def test_fleet_plus_loop_is_usage(fake_ctx):
    with pytest.raises(CliError) as ei:
        heal.run(
            fake_ctx,
            SimpleNamespace(
                fleet=True,
                loop=True,
                watchdog=False,
                together=False,
                dry_run=False,
                peer=None,
                interval=None,
            ),
        )
    assert ei.value.exit_code == USAGE
