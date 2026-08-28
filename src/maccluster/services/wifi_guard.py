"""Keep Wi-Fi as the default route when the Thunderbolt Bridge is up.

`networksetup` lets the TB Bridge service carry a `Router` (IPv4 gateway).
macOS ranks routes by service order, so a TB Bridge with a Router set
outranks Wi-Fi and becomes the default gateway — Wi-Fi internet drops even
though the TB link only reaches the other Mac mini, never the internet.
"""

from __future__ import annotations

PREFS_PLIST = "/Library/Preferences/SystemConfiguration/preferences.plist"
TB_SERVICE = "Thunderbolt Bridge"

_WIFI_MARKERS = ("wi-fi", "wifi", "airport")


def parse_networksetup_info(stdout: str) -> dict[str, str]:
    """Parse `networksetup -getinfo <service>` output into a field dict."""
    fields: dict[str, str] = {}
    for line in stdout.splitlines():
        line = line.strip()
        if not line or ":" not in line:
            continue
        key, _, value = line.partition(":")
        fields[key.strip()] = value.strip()
    return fields


def parse_networksetup_services(stdout: str) -> list[str]:
    """Parse `networksetup -listallnetworkservices` output into service names.

    Skips the leading advisory line and strips the `*` disabled-service marker.
    """
    names: list[str] = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line or line.startswith("An asterisk"):
            continue
        names.append(line.lstrip("*").strip())
    return names


def router_steals_internet(router: str | None, cluster_ip: str | None = None) -> bool:
    """True when the TB Bridge's Router field would hijack the default route.

    Any non-empty Router on the TB Bridge is a problem: the bridge only ever
    reaches the cluster peer, never the internet. `cluster_ip` is accepted
    for call-site symmetry with `protect_wifi_from_bridge` but does not
    change the verdict — a router pointed at the peer's own Self-IP is just
    as harmful as any other address.
    """
    if not router:
        return False
    router = router.strip()
    if not router or router in ("0.0.0.0", "none", "None"):
        return False
    return True


def order_wifi_first_tb_last(names: list[str]) -> list[str]:
    """Reorder services: Wi-Fi first, Thunderbolt Bridge last, rest unchanged."""
    wifi = [n for n in names if any(m in n.lower() for m in _WIFI_MARKERS)]
    tb = [n for n in names if n == TB_SERVICE]
    rest = [n for n in names if n not in wifi and n != TB_SERVICE]
    return wifi + rest + tb
