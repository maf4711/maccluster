"""up command output."""

from __future__ import annotations

from types import SimpleNamespace

from maccluster.adapters.tb_ioreg import FakeTB
from maccluster.commands.up import run
from maccluster.domain.enums import LinkState
from maccluster.domain.models import ThunderboltPort, ThunderboltSnapshot
from maccluster.services.mutate_service import ensure_local


def _two_link_snapshot() -> ThunderboltSnapshot:
    def port(receptacle: str, uuid: str) -> ThunderboltPort:
        return ThunderboltPort(
            receptacle_id=receptacle,
            interface_name="bridge0",
            capable=True,
            thunderbolt_version="USB4",
            link_speed_gbps=40.0,
            link_state=LinkState.CONNECTED,
            domain_uuid=uuid,
        )

    return ThunderboltSnapshot(
        ports=(
            port("2", "BBBBBBBB-BBBB-BBBB-BBBB-BBBBBBBBBBBB"),
            port("3", "CCCCCCCC-CCCC-CCCC-CCCC-CCCCCCCCCCCC"),
        ),
        source="fake",
        host_model="MacBook Pro",
    )


def test_ensure_local_counts_connected_tb_links(fake_ctx):
    fake_ctx.tb = FakeTB(_two_link_snapshot())
    result = ensure_local(fake_ctx)
    assert result.tb_links == 2


def test_up_prints_tb_link_count(fake_ctx, capsys):
    fake_ctx.tb = FakeTB(_two_link_snapshot())
    assert run(fake_ctx, SimpleNamespace(dry_run=False)) == 0
    out = capsys.readouterr().out
    assert "interface=bridge0 ip=10.42.0.1 tb_links=2" in out
