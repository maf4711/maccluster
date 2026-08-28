"""Network mutation via ifconfig (local Self only, allowlisted iface)."""

from __future__ import annotations

from ipaddress import IPv4Address

from maccluster.constants import TIMEOUT_GENERIC
from maccluster.domain.invariants import is_valid_iface_name
from maccluster.errors import CliError, PrivilegeError
from maccluster.ports.process import ProcessRunnerPort


class NetworkApply:
    def __init__(self, runner: ProcessRunnerPort) -> None:
        self._runner = runner

    def _validate_iface(self, interface: str) -> None:
        if not is_valid_iface_name(interface):
            raise CliError(f"invalid interface name: {interface!r}", exit_code=2)
        # Refuse obvious Wi-Fi / primary that we never manage as TB bridge by default
        # (operator may still set bridge0 etc.)

    def admin_up(self, interface: str, *, dry_run: bool = False) -> None:
        self._validate_iface(interface)
        if dry_run:
            return
        result = self._runner.run(
            ["ifconfig", interface, "up"],
            timeout=TIMEOUT_GENERIC,
        )
        if result.returncode != 0:
            self._raise_privilege_or_fail(result.stderr or result.stdout)

    def ensure_bridge_and_ip(
        self,
        interface: str,
        ip: IPv4Address,
        *,
        prefixlen: int,
        dry_run: bool = False,
    ) -> None:
        self._validate_iface(interface)
        if dry_run:
            return
        # Ensure interface exists / up
        check = self._runner.run(["ifconfig", interface], timeout=TIMEOUT_GENERIC)
        if check.returncode != 0:
            # try create bridge (may need root)
            create = self._runner.run(
                ["ifconfig", interface, "create"],
                timeout=TIMEOUT_GENERIC,
            )
            if create.returncode != 0:
                self._raise_privilege_or_fail(
                    create.stderr or create.stdout or f"cannot create {interface}"
                )

        up = self._runner.run(["ifconfig", interface, "up"], timeout=TIMEOUT_GENERIC)
        if up.returncode != 0:
            self._raise_privilege_or_fail(up.stderr or up.stdout)

        # Check if IP already present
        show = self._runner.run(["ifconfig", interface], timeout=TIMEOUT_GENERIC)
        if str(ip) in (show.stdout or ""):
            return

        # macOS ifconfig: ifconfig bridge0 inet 10.42.0.1 netmask 255.255.255.0
        from ipaddress import IPv4Network

        net = IPv4Network(f"{ip}/{prefixlen}", strict=False)
        netmask = str(net.netmask)
        add = self._runner.run(
            ["ifconfig", interface, "inet", str(ip), "netmask", netmask],
            timeout=TIMEOUT_GENERIC,
        )
        if add.returncode != 0:
            # try alias form
            add2 = self._runner.run(
                ["ifconfig", interface, "inet", f"{ip}/{prefixlen}"],
                timeout=TIMEOUT_GENERIC,
            )
            if add2.returncode != 0:
                self._raise_privilege_or_fail(add2.stderr or add.stderr or add.stdout)

    def protect_wifi_from_bridge(self, cluster_ip: str, *, dry_run: bool = False) -> None:
        """TB Bridge must not be a default gateway. Wi-Fi stays first.

        Best-effort: missing admin rights are ignored so unprivileged heal
        still reports IP status. Run `sudo maccluster up` to persist prefs.
        """
        from maccluster.services.wifi_guard import (
            PREFS_PLIST,
            TB_SERVICE,
            order_wifi_first_tb_last,
            parse_networksetup_info,
            parse_networksetup_services,
            router_steals_internet,
        )

        if dry_run:
            return
        info = self._runner.run(
            ["/usr/sbin/networksetup", "-getinfo", TB_SERVICE],
            timeout=TIMEOUT_GENERIC,
        )
        fields = parse_networksetup_info(info.stdout or "")
        if router_steals_internet(fields.get("Router"), cluster_ip):
            uuid = self._tb_service_uuid()
            if uuid:
                pb = self._runner.run(
                    [
                        "/usr/libexec/PlistBuddy",
                        "-c",
                        f"Delete :NetworkServices:{uuid}:IPv4:Router",
                        PREFS_PLIST,
                    ],
                    timeout=TIMEOUT_GENERIC,
                )
                if pb.returncode != 0:
                    err = (pb.stderr or pb.stdout or "").lower()
                    if "permission" in err or "not permitted" in err:
                        return

        listed = self._runner.run(
            ["/usr/sbin/networksetup", "-listallnetworkservices"],
            timeout=TIMEOUT_GENERIC,
        )
        names = parse_networksetup_services(listed.stdout or "")
        wanted = order_wifi_first_tb_last(names)
        if wanted and wanted != names:
            self._runner.run(
                ["/usr/sbin/networksetup", "-ordernetworkservices", *wanted],
                timeout=TIMEOUT_GENERIC,
            )

    def _tb_service_uuid(self) -> str | None:
        import plistlib
        from pathlib import Path

        from maccluster.services.wifi_guard import PREFS_PLIST, TB_SERVICE

        try:
            data = plistlib.loads(Path(PREFS_PLIST).read_bytes())
        except OSError:
            return None
        for uuid, svc in (data.get("NetworkServices") or {}).items():
            if (svc.get("UserDefinedName") or "") == TB_SERVICE:
                return str(uuid)
        return None

    @staticmethod
    def _raise_privilege_or_fail(msg: str) -> None:
        low = msg.lower()
        if any(
            x in low
            for x in (
                "permission denied",
                "operation not permitted",
                "must be root",
                "not permitted",
            )
        ):
            raise PrivilegeError(
                "admin/sudo required to modify network interfaces",
                details=msg.strip(),
            )
        raise CliError(f"network apply failed: {msg.strip()}", exit_code=1)


class FakeNetworkApply:
    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.fail_privilege = False
        self.fail = False

    def admin_up(self, interface: str, *, dry_run: bool = False) -> None:
        self.calls.append(("admin_up", interface, dry_run))
        if self.fail_privilege:
            raise PrivilegeError()
        if self.fail:
            raise CliError("fake apply failed", exit_code=1)

    def ensure_bridge_and_ip(
        self,
        interface: str,
        ip: IPv4Address,
        *,
        prefixlen: int,
        dry_run: bool = False,
    ) -> None:
        self.calls.append(("ensure", interface, str(ip), prefixlen, dry_run))
        if self.fail_privilege:
            raise PrivilegeError()
        if self.fail:
            raise CliError("fake apply failed", exit_code=1)

    def protect_wifi_from_bridge(self, cluster_ip: str, *, dry_run: bool = False) -> None:
        self.calls.append(("protect_wifi", cluster_ip, dry_run))
