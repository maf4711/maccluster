"""CLI: doctor --host --fleet flags."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from maccluster.cli.exit_codes import USAGE
from maccluster.cli.parser import build_parser
from maccluster.commands import doctor
from maccluster.errors import CliError


def test_parse_host_fleet_flags():
    p = build_parser()
    args = p.parse_args(["doctor", "--host", "--fleet", "--peer", "node-b"])
    assert args.host is True
    assert args.fleet is True
    assert args.peer == "node-b"


def test_fleet_requires_host(fake_ctx):
    with pytest.raises(CliError) as ei:
        doctor.run(
            fake_ctx,
            SimpleNamespace(exo=False, exo_url=None, host=False, fleet=True, peer=None),
        )
    assert ei.value.exit_code == USAGE
    assert "--host" in ei.value.message


def test_peer_requires_fleet(fake_ctx):
    with pytest.raises(CliError) as ei:
        doctor.run(
            fake_ctx,
            SimpleNamespace(exo=False, exo_url=None, host=True, fleet=False, peer="node-b"),
        )
    assert ei.value.exit_code == USAGE
