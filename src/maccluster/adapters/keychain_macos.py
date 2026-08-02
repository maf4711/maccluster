"""macOS Keychain access via `security` (login keychain).

Items are stored in the **local** login keychain only. The ``security`` CLI
cannot create iCloud-synchronizable generic-password items, so peers do **not**
see these entries automatically. Use ``maccluster keychain push-peer`` or
``remote-install`` to put config on another Mac.

Note: ``security -w`` hex-encodes secrets that contain newlines. We store config
as ``b64:…`` (base64 UTF-8) so TOML round-trips cleanly.
"""

from __future__ import annotations

import base64
import binascii
import re

from maccluster.constants import (
    KEYCHAIN_ACCOUNT_DEFAULT,
    KEYCHAIN_SERVICE_CONFIG,
    KEYCHAIN_SERVICE_SSH_PASSWORD,
    KEYCHAIN_SERVICE_SSH_USER,
    TIMEOUT_GENERIC,
)
from maccluster.errors import CliError
from maccluster.ports.process import ProcessRunnerPort

_B64_PREFIX = "b64:"
_HEX_RE = re.compile(r"^[0-9a-fA-F]+$")


def encode_secret(text: str) -> str:
    """Encode for Keychain storage (survives security -w hex mode)."""
    raw = base64.b64encode(text.encode("utf-8")).decode("ascii")
    return _B64_PREFIX + raw


def decode_secret(stored: str) -> str:
    """Decode Keychain payload (b64: prefix, or legacy hex from security -w)."""
    s = (stored or "").strip()
    if not s:
        return s
    if s.startswith(_B64_PREFIX):
        return base64.b64decode(s[len(_B64_PREFIX) :].encode("ascii")).decode("utf-8")
    # security find-generic-password -w often returns hex for multiline secrets
    if _HEX_RE.fullmatch(s) and len(s) % 2 == 0 and len(s) >= 8:
        try:
            decoded = binascii.unhexlify(s).decode("utf-8")
            if "schema_version" in decoded or "\n" in decoded or decoded.isprintable():
                return decoded
        except (binascii.Error, UnicodeDecodeError):
            pass
    return s


class KeychainStore:
    """Thin wrapper around `security` for generic-password items."""

    def __init__(self, runner: ProcessRunnerPort) -> None:
        self._runner = runner

    def _security(self, *args: str, check: bool = False) -> tuple[int, str, str]:
        try:
            abs_sec = self._runner.resolve("security")
        except CliError as exc:
            raise CliError("security tool not found (macOS Keychain)", exit_code=1) from exc
        result = self._runner.run([abs_sec, *args], timeout=TIMEOUT_GENERIC, check=False)
        return result.returncode, (result.stdout or "").strip(), (result.stderr or "").strip()

    def get_password(self, *, service: str, account: str = KEYCHAIN_ACCOUNT_DEFAULT) -> str | None:
        rc, out, err = self._security(
            "find-generic-password",
            "-a",
            account,
            "-s",
            service,
            "-w",
        )
        if rc != 0:
            # item not found is common
            if "could not be found" in (err + out).lower() or rc == 44:
                return None
            return None
        if not out:
            return None
        return decode_secret(out)

    def set_password(
        self,
        *,
        service: str,
        password: str,
        account: str = KEYCHAIN_ACCOUNT_DEFAULT,
        label: str | None = None,
        raw: bool = False,
    ) -> None:
        # Encode multiline / config payloads as b64.
        payload = password if raw else encode_secret(password)
        # Never use -U: updating an existing item rewrites its ACL, which can
        # block on a Keychain UI prompt (times out rc 124) and lose the item.
        # A fresh add by the calling tool never prompts.
        self.delete_password(service=service, account=account)
        args = [
            "add-generic-password",
            "-a",
            account,
            "-s",
            service,
            "-w",
            payload,
        ]
        if label:
            args.extend(["-l", label])
        rc, out, err = self._security(*args)
        if rc != 0:
            detail = err or out or str(rc)
            if "authorization was denied" in detail.lower():
                hint = (
                    " — the login keychain is locked (SSH sessions cannot write to it): "
                    "log in on that Mac and rerun, or first run "
                    "`security unlock-keychain ~/Library/Keychains/login.keychain-db`"
                )
            elif rc == 124:
                hint = " — write timed out (Keychain UI prompt pending?)"
            else:
                hint = ""
            raise CliError(
                f"keychain write failed for {service}: {detail}{hint}",
                exit_code=1,
            )

    def delete_password(self, *, service: str, account: str = KEYCHAIN_ACCOUNT_DEFAULT) -> bool:
        rc, _out, _err = self._security(
            "delete-generic-password",
            "-a",
            account,
            "-s",
            service,
        )
        return rc == 0

    # --- MacCluster helpers ---

    def get_cluster_config_toml(self, account: str = KEYCHAIN_ACCOUNT_DEFAULT) -> str | None:
        return self.get_password(service=KEYCHAIN_SERVICE_CONFIG, account=account)

    def set_cluster_config_toml(
        self, text: str, *, account: str = KEYCHAIN_ACCOUNT_DEFAULT, cluster_name: str = ""
    ) -> None:
        label = f"MacCluster config {cluster_name}".strip() or "MacCluster config"
        self.set_password(
            service=KEYCHAIN_SERVICE_CONFIG,
            password=text,
            account=account,
            label=label,
        )

    def get_ssh_user(self, account: str = KEYCHAIN_ACCOUNT_DEFAULT) -> str | None:
        return self.get_password(service=KEYCHAIN_SERVICE_SSH_USER, account=account)

    def set_ssh_user(self, user: str, *, account: str = KEYCHAIN_ACCOUNT_DEFAULT) -> None:
        self.set_password(
            service=KEYCHAIN_SERVICE_SSH_USER,
            password=user.strip(),
            account=account,
            label="MacCluster SSH user",
            raw=True,  # single-line username
        )

    def get_ssh_password(self, account: str = KEYCHAIN_ACCOUNT_DEFAULT) -> str | None:
        return self.get_password(service=KEYCHAIN_SERVICE_SSH_PASSWORD, account=account)

    def set_ssh_password(self, password: str, *, account: str = KEYCHAIN_ACCOUNT_DEFAULT) -> None:
        self.set_password(
            service=KEYCHAIN_SERVICE_SSH_PASSWORD,
            password=password,
            account=account,
            label="MacCluster SSH password (bootstrap)",
            raw=True,
        )

    def delete_all(self, account: str = KEYCHAIN_ACCOUNT_DEFAULT) -> list[str]:
        removed: list[str] = []
        for svc in (
            KEYCHAIN_SERVICE_CONFIG,
            KEYCHAIN_SERVICE_SSH_USER,
            KEYCHAIN_SERVICE_SSH_PASSWORD,
        ):
            if self.delete_password(service=svc, account=account):
                removed.append(svc)
        return removed


class FakeKeychainStore:
    """In-memory keychain for tests."""

    def __init__(self) -> None:
        self._data: dict[tuple[str, str], str] = {}

    def get_password(self, *, service: str, account: str = KEYCHAIN_ACCOUNT_DEFAULT) -> str | None:
        return self._data.get((service, account))

    def set_password(
        self,
        *,
        service: str,
        password: str,
        account: str = KEYCHAIN_ACCOUNT_DEFAULT,
        label: str | None = None,
        raw: bool = False,
    ) -> None:
        del label, raw  # API parity with KeychainStore; in-memory stores plaintext
        self._data[(service, account)] = password

    def delete_password(self, *, service: str, account: str = KEYCHAIN_ACCOUNT_DEFAULT) -> bool:
        return self._data.pop((service, account), None) is not None

    def get_cluster_config_toml(self, account: str = KEYCHAIN_ACCOUNT_DEFAULT) -> str | None:
        return self.get_password(service=KEYCHAIN_SERVICE_CONFIG, account=account)

    def set_cluster_config_toml(
        self, text: str, *, account: str = KEYCHAIN_ACCOUNT_DEFAULT, cluster_name: str = ""
    ) -> None:
        del cluster_name
        self.set_password(service=KEYCHAIN_SERVICE_CONFIG, password=text, account=account)

    def get_ssh_user(self, account: str = KEYCHAIN_ACCOUNT_DEFAULT) -> str | None:
        return self.get_password(service=KEYCHAIN_SERVICE_SSH_USER, account=account)

    def set_ssh_user(self, user: str, *, account: str = KEYCHAIN_ACCOUNT_DEFAULT) -> None:
        self.set_password(
            service=KEYCHAIN_SERVICE_SSH_USER, password=user, account=account, raw=True
        )

    def get_ssh_password(self, account: str = KEYCHAIN_ACCOUNT_DEFAULT) -> str | None:
        return self.get_password(service=KEYCHAIN_SERVICE_SSH_PASSWORD, account=account)

    def set_ssh_password(self, password: str, *, account: str = KEYCHAIN_ACCOUNT_DEFAULT) -> None:
        self.set_password(
            service=KEYCHAIN_SERVICE_SSH_PASSWORD,
            password=password,
            account=account,
            raw=True,
        )

    def delete_all(self, account: str = KEYCHAIN_ACCOUNT_DEFAULT) -> list[str]:
        removed = []
        for svc in (
            KEYCHAIN_SERVICE_CONFIG,
            KEYCHAIN_SERVICE_SSH_USER,
            KEYCHAIN_SERVICE_SSH_PASSWORD,
        ):
            if self.delete_password(service=svc, account=account):
                removed.append(svc)
        return removed
