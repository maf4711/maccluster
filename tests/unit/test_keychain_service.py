"""Keychain service (fake store)."""

from __future__ import annotations

from pathlib import Path

import pytest

from maccluster.adapters.keychain_macos import FakeKeychainStore
from maccluster.constants import KEYCHAIN_SERVICE_CONFIG, KEYCHAIN_SERVICE_SSH_USER
from maccluster.errors import CliError


def test_encode_decode_secret():
    from maccluster.adapters.keychain_macos import decode_secret, encode_secret

    raw = 'schema_version = 1\nname = "x"\n'
    enc = encode_secret(raw)
    assert enc.startswith("b64:")
    assert decode_secret(enc) == raw
    # legacy hex (security -w style)
    hx = raw.encode().hex()
    assert decode_secret(hx) == raw


def test_fake_keychain_roundtrip():
    kc = FakeKeychainStore()
    assert kc.get_cluster_config_toml() is None
    kc.set_cluster_config_toml('schema_version = 1\nname = "t"\n', cluster_name="t")
    assert "schema_version" in (kc.get_cluster_config_toml() or "")
    kc.set_ssh_user("mafoe")
    assert kc.get_ssh_user() == "mafoe"
    kc.set_ssh_password("secret")
    assert kc.get_ssh_password() == "secret"
    removed = kc.delete_all()
    assert KEYCHAIN_SERVICE_CONFIG in removed
    assert KEYCHAIN_SERVICE_SSH_USER in removed
    assert kc.get_ssh_password() is None


def test_init_prefers_keychain(fake_ctx, tmp_path: Path, monkeypatch):
    from maccluster.services import keychain_service as ks
    from maccluster.services.init_service import init_cluster

    fake = FakeKeychainStore()
    toml = """schema_version = 1
name = "from-kc"
subnet = "10.42.0.0/24"
bridge_interface = "bridge0"
heal_interval_seconds = 30
ssh_probes_enabled = false

[[nodes]]
id = "node-a"
hostnames = ["mac-mini-a", "mac-mini-a.local"]
ip = "10.42.0.1"
hw_uuid = "00000000-0000-0000-0000-000000000001"

[[nodes]]
id = "node-b"
hostnames = ["mac-mini-b", "mac-mini-b.local"]
ip = "10.42.0.2"
hw_uuid = "00000000-0000-0000-0000-000000000002"
"""
    fake.set_cluster_config_toml(toml, cluster_name="from-kc")
    fake.set_ssh_user("mafoe")

    def _fake_kc(ctx):
        return fake

    monkeypatch.setattr(ks, "_kc", _fake_kc)
    monkeypatch.setattr(
        "maccluster.services.init_service.show_keychain",
        lambda ctx, account="default": ks.show_keychain(ctx, account=account),
    )
    # re-bind show_keychain inside init uses keychain_service._kc via pull/push
    monkeypatch.setattr(
        "maccluster.services.init_service.pull_config_from_keychain", ks.pull_config_from_keychain
    )
    monkeypatch.setattr("maccluster.services.keychain_service._kc", _fake_kc)

    cfg_path = tmp_path / "cluster.toml"
    fake_ctx.config_path = cfg_path
    path, source = init_cluster(fake_ctx, force=True, from_keychain=True, save_keychain=False)
    assert source == "keychain"
    assert path.exists()
    assert "from-kc" in path.read_text()


def test_pull_missing_raises(fake_ctx, tmp_path, monkeypatch):
    from maccluster.services import keychain_service as ks

    fake = FakeKeychainStore()
    monkeypatch.setattr(ks, "_kc", lambda ctx: fake)
    fake_ctx.config_path = tmp_path / "cluster.toml"
    with pytest.raises(CliError):
        ks.pull_config_from_keychain(fake_ctx)


class _RecordingRunner:
    """Records security argv; add succeeds, delete reports not-found."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def resolve(self, basename: str) -> str:
        return f"/usr/bin/{basename}"

    def run(self, argv, *, timeout, check=False):
        from maccluster.ports.process import ProcessResult

        self.calls.append(list(argv))
        rc = 44 if "delete-generic-password" in argv else 0
        return ProcessResult(argv=tuple(argv), returncode=rc, stdout="", stderr="")


def test_set_password_deletes_then_adds_without_update_flag():
    """-U on an existing item can hang on a Keychain ACL prompt (rc 124)
    and destroy the item; a fresh add after delete never prompts."""
    from maccluster.adapters.keychain_macos import KeychainStore

    runner = _RecordingRunner()
    kc = KeychainStore(runner)
    kc.set_password(service="svc", password="geheim", account="acct")
    actions = [c[1] for c in runner.calls]
    assert actions == ["delete-generic-password", "add-generic-password"]
    add = runner.calls[1]
    assert "-U" not in add
    assert "" not in add  # no empty -T arg
