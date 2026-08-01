"""system_profiler TB parser."""

from __future__ import annotations

from maccluster.adapters.tb_system_profiler import parse_system_profiler_tb
from maccluster.domain.enums import LinkState


def test_parse_sample(sample_tb_text: str):
    snap = parse_system_profiler_tb(sample_tb_text)
    assert snap.source == "system_profiler"
    assert len(snap.ports) >= 1
    for p in snap.ports:
        assert p.receptacle_id
        assert p.capable is True
        assert p.link_state in (
            LinkState.CONNECTED,
            LinkState.UNCONNECTED,
            LinkState.UNKNOWN,
        )


def test_parse_unconnected_ports():
    text = """
Thunderbolt/USB4:

    Thunderbolt/USB4 Bus 0:

      Vendor Name: Apple Inc.
      Device Name: Mac mini
      UID: 0x1
      Domain UUID: AAAA-BBBB
      Port:
          Status: No device connected
          Speed: Up to 40 Gb/s
          Receptacle: 1
"""
    snap = parse_system_profiler_tb(text)
    assert len(snap.ports) == 1
    assert snap.ports[0].link_state == LinkState.UNCONNECTED
    assert snap.ports[0].receptacle_id == "1"
    assert snap.ports[0].link_speed_gbps == 40.0
