"""Parse peer identity (nested device block) from system_profiler output."""

from __future__ import annotations

from maccluster.adapters.tb_system_profiler import parse_system_profiler_tb
from maccluster.domain.enums import LinkState

# Real macOS 27 format: nested attached-device block carries the peer's
# model code and the peer port's own Domain UUID.
SAMPLE = """Thunderbolt/USB4:

    Thunderbolt/USB4 Bus 2:

      Vendor Name: Apple Inc.
      Device Name: MacBook Pro
      UID: 0x05AC20D5BAF28992
      Route String: 0
      Domain UUID: 9EFBA377-528E-4A87-B974-913EE77BCB9A
      Port:
          Status: Device connected
          Link Status: 0x2
          Speed: 40 Gb/s
          Receptacle: 3
          Micro Firmware Version: 0.0.0

        Mac mini:

          Vendor Name: Apple Inc.
          Device Name: Mac16,11
          Device ID: 0xA
          Vendor ID: 0x0A27
          Domain UUID: E4CCB4B9-724D-4728-ADE2-F356148F8F79
          Services:
            Internet Protocol:
              Protocol ID: 1

    Thunderbolt/USB4 Bus 1:

      Vendor Name: Apple Inc.
      Device Name: MacBook Pro
      UID: 0x05AC20D5BAF28991
      Route String: 0
      Domain UUID: 68947458-9A96-4930-9E4F-9D614759AE6E
      Port:
          Status: Device connected
          Link Status: 0x2
          Speed: 40 Gb/s
          Receptacle: 2

        Mac mini:

          Vendor Name: Apple Inc.
          Device Name: Mac16,11
          Domain UUID: E9F38DFF-9A9A-4A0A-8D9C-02C3325633C0

    Thunderbolt/USB4 Bus 0:

      Vendor Name: Apple Inc.
      Device Name: MacBook Pro
      UID: 0x05AC20D5BAF28990
      Route String: 0
      Domain UUID: A2D8DC5B-03DF-43B6-A7C4-8848574C913E
      Port:
          Status: No device connected
          Link Status: 0x100
          Speed: Up to 120 Gb/s
          Receptacle: 1
"""


def _port(snap, receptacle):
    return next(p for p in snap.ports if p.receptacle_id == receptacle)


def test_connected_port_reports_peer_model_and_peer_domain_uuid():
    snap = parse_system_profiler_tb(SAMPLE)
    p3 = _port(snap, "3")
    assert p3.link_state == LinkState.CONNECTED
    assert p3.peer_name == "Mac16,11"
    assert p3.domain_uuid == "9EFBA377-528E-4A87-B974-913EE77BCB9A"
    assert p3.peer_domain_uuid == "E4CCB4B9-724D-4728-ADE2-F356148F8F79"

    p2 = _port(snap, "2")
    assert p2.peer_name == "Mac16,11"
    assert p2.peer_domain_uuid == "E9F38DFF-9A9A-4A0A-8D9C-02C3325633C0"


def test_local_generic_device_name_is_not_a_peer():
    snap = parse_system_profiler_tb(SAMPLE)
    for port in snap.ports:
        assert port.peer_name != "MacBook Pro"


def test_unconnected_port_has_no_peer_fields():
    snap = parse_system_profiler_tb(SAMPLE)
    p1 = _port(snap, "1")
    assert p1.link_state == LinkState.UNCONNECTED
    assert p1.peer_name is None
    assert p1.peer_domain_uuid is None
    assert p1.domain_uuid == "A2D8DC5B-03DF-43B6-A7C4-8848574C913E"
