"""Traffic rate computation and netstat parsing."""

from __future__ import annotations

from pathlib import Path

from maccluster.domain.models import InterfaceCounters
from maccluster.health.traffic import (
    compute_traffic,
    format_bps,
    format_pps,
    parse_netstat_ib,
)


def test_parse_netstat_link_row(fixtures_dir: Path):
    text = (fixtures_dir / "netstat" / "bridge0_ib.txt").read_text()
    parsed = parse_netstat_ib(text, t_mono=10.0)
    assert "bridge0" in parsed
    c = parsed["bridge0"]
    assert c.ibytes == 10_000_000
    assert c.obytes == 5_000_000
    assert c.ipkts == 1000
    assert c.opkts == 2000
    assert c.ierrs == 2
    assert c.oerrs == 1
    assert c.t_mono == 10.0


def test_compute_rates():
    prev = InterfaceCounters(
        name="bridge0",
        ipkts=1000,
        ierrs=0,
        ibytes=1_000_000,
        opkts=500,
        oerrs=0,
        obytes=500_000,
        t_mono=1.0,
    )
    curr = InterfaceCounters(
        name="bridge0",
        ipkts=2000,
        ierrs=1,
        ibytes=2_000_000,
        opkts=1000,
        oerrs=2,
        obytes=1_000_000,
        t_mono=2.0,
    )
    t = compute_traffic(curr, prev)
    assert t.rate_available
    assert t.sample_dt_s == 1.0
    assert t.rx_bps == 8_000_000.0  # 1e6 bytes * 8
    assert t.tx_bps == 4_000_000.0
    assert t.rx_pps == 1000.0
    assert t.tx_pps == 500.0
    assert t.ierrs_delta == 1
    assert t.oerrs_delta == 2


def test_compute_no_prev():
    curr = InterfaceCounters(
        name="bridge0",
        ipkts=1,
        ierrs=0,
        ibytes=100,
        opkts=1,
        oerrs=0,
        obytes=50,
        t_mono=5.0,
    )
    t = compute_traffic(curr, None)
    assert not t.rate_available
    assert t.rx_bps is None


def test_format_helpers():
    assert "Mb/s" in format_bps(12_500_000)
    assert "Gb/s" in format_bps(2_000_000_000)
    assert "k pps" in format_pps(2500) or "pps" in format_pps(2500)
