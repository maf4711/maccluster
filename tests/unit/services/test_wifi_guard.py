"""TB Bridge must not steal the default route from Wi-Fi."""

from __future__ import annotations

from maccluster.services.wifi_guard import (
    TB_SERVICE,
    order_wifi_first_tb_last,
    parse_networksetup_info,
    parse_networksetup_services,
    router_steals_internet,
)


def test_router_null_is_not_a_router():
    # Real `networksetup -getinfo` output on an unconfigured service.
    assert router_steals_internet("(null)") is False


def test_router_none_or_empty_is_not_a_router():
    assert router_steals_internet(None) is False
    assert router_steals_internet("") is False
    assert router_steals_internet("0.0.0.0") is False


def test_router_set_steals_internet():
    assert router_steals_internet("10.42.0.1") is True


def test_parse_networksetup_info_handles_null_router():
    stdout = (
        "Manual Configuration\n"
        "IP address: 10.42.0.2\n"
        "Subnet mask: 255.255.255.0\n"
        "Router: (null)\n"
        "IPv6: Automatic\n"
    )
    fields = parse_networksetup_info(stdout)
    assert fields["Router"] == "(null)"
    assert router_steals_internet(fields["Router"]) is False


def test_parse_networksetup_services_skips_advisory_and_strips_marker():
    stdout = (
        "An asterisk (*) denotes that a network service is disabled.\n"
        "Wi-Fi\n"
        "*Bluetooth PAN\n"
        "Thunderbolt Bridge\n"
    )
    assert parse_networksetup_services(stdout) == ["Wi-Fi", "Bluetooth PAN", "Thunderbolt Bridge"]


def test_order_wifi_first_tb_last():
    names = ["Ethernet", "Thunderbolt Bridge", "Bluetooth PAN", "Wi-Fi"]
    assert order_wifi_first_tb_last(names) == [
        "Wi-Fi",
        "Ethernet",
        "Bluetooth PAN",
        TB_SERVICE,
    ]
