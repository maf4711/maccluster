"""The rung selected on delta/pull/push must reach sync_home.

Parsing the flag is not enough: both commands rebuild their arguments into a
fresh SimpleNamespace before calling sync_cmd, so a field that is not copied
there is silently dropped and the run falls back to the tb default.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from maccluster.cli.parser import build_parser
from maccluster.commands import delta_cmd, home_dev_transfer, sync_cmd


@pytest.mark.parametrize("command", ["delta", "pull", "push"])
def test_transport_reaches_sync_home(command, monkeypatch):
    seen: dict[str, object] = {}

    def fake_run(ctx, args):
        seen["transport"] = getattr(args, "transport", "MISSING")
        return 0

    monkeypatch.setattr(sync_cmd, "run", fake_run)
    args = build_parser().parse_args([command, "--transport", "wifi"])
    ctx = SimpleNamespace(json_mode=True)

    if command == "delta":
        delta_cmd.run(ctx, args)
    else:
        home_dev_transfer.run_transfer(ctx, args, command=command)

    assert seen["transport"] == "wifi"
