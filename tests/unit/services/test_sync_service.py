"""Home sync service (Apple ditto, newest-wins)."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pytest

from maccluster.errors import CliError
from maccluster.ports.process import ProcessResult
from maccluster.services.sync_service import (
    FileMeta,
    exit_code_for_sync,
    inventory_local,
    is_excluded,
    parse_inventory_text,
    plan_transfers,
    sync_home,
)


def test_is_excluded_caches_and_glob():
    assert is_excluded("Library/Caches/foo", ("Library/Caches/",))
    assert is_excluded("proj/node_modules/x", ("**/node_modules/",))
    assert is_excluded(".DS_Store", (".DS_Store",))
    assert not is_excluded("Documents/note.txt", ("Library/Caches/",))


def test_plan_transfers_newest_wins():
    local = {
        "a.txt": FileMeta(mtime_ns=200, size=1),
        "b.txt": FileMeta(mtime_ns=100, size=1),
        "only-local.txt": FileMeta(mtime_ns=1, size=1),
    }
    remote = {
        "a.txt": FileMeta(mtime_ns=100, size=1),  # local newer → push
        "b.txt": FileMeta(mtime_ns=300, size=1),  # remote newer → pull
        "only-remote.txt": FileMeta(mtime_ns=1, size=1),
        "equal.txt": FileMeta(mtime_ns=50, size=1),
    }
    local["equal.txt"] = FileMeta(mtime_ns=50, size=1)
    push, pull = plan_transfers(local, remote)
    assert "a.txt" in push and "only-local.txt" in push
    assert "b.txt" not in push
    assert "b.txt" in pull and "only-remote.txt" in pull
    assert "equal.txt" not in push and "equal.txt" not in pull


def test_inventory_local_respects_excludes(tmp_path: Path):
    (tmp_path / "Documents").mkdir()
    (tmp_path / "Documents" / "ok.txt").write_text("x", encoding="utf-8")
    (tmp_path / "Library").mkdir()
    (tmp_path / "Library" / "Caches").mkdir()
    (tmp_path / "Library" / "Caches" / "big").write_text("z", encoding="utf-8")
    inv = inventory_local(tmp_path, ("Library/Caches/",))
    assert "Documents/ok.txt" in inv
    assert not any(k.startswith("Library/Caches") for k in inv)


def test_parse_inventory_text():
    text = "Documents/a.txt\t123\t10\nbadline\nfoo\tnotint\t1\n"
    inv = parse_inventory_text(text)
    assert inv["Documents/a.txt"].mtime_ns == 123
    assert inv["Documents/a.txt"].size == 10


class RecordingRunner:
    def __init__(self, *, fail_ssh: bool = False) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.fail_ssh = fail_ssh

    def resolve(self, basename: str) -> str:
        if basename in ("ssh", "scp", "ditto"):
            return f"/usr/bin/{basename}"
        raise CliError(f"tool not found: {basename}", exit_code=1)

    def run(
        self,
        argv: Sequence[str],
        *,
        timeout: float = 15.0,
        check: bool = False,
    ) -> ProcessResult:
        full = tuple(argv)
        self.calls.append(full)
        name = Path(full[0]).name
        if name == "ssh":
            # preflight true
            if full[-1] in ("/usr/bin/true", "true") or (
                len(full) >= 2 and full[-1] == "/usr/bin/true"
            ):
                rc = 255 if self.fail_ssh else 0
                return ProcessResult(
                    argv=full,
                    returncode=rc,
                    stdout="",
                    stderr="Permission denied" if self.fail_ssh else "",
                )
            # remote inventory python — empty inventory
            if "python3" in full:
                return ProcessResult(argv=full, returncode=0, stdout="", stderr="")
            # sh -c cleanup / extract
            return ProcessResult(argv=full, returncode=0, stdout="staged=0 archive_rc=0", stderr="")
        if name == "scp":
            return ProcessResult(argv=full, returncode=0, stdout="", stderr="")
        if name == "ditto":
            return ProcessResult(argv=full, returncode=0, stdout="", stderr="")
        return ProcessResult(argv=full, returncode=1, stdout="", stderr="unknown")


def test_sync_home_ssh_fail(fake_ctx, tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()
    (home / "f.txt").write_text("hi", encoding="utf-8")
    fake_ctx.runner = RecordingRunner(fail_ssh=True)
    result = sync_home(
        fake_ctx,
        peer="node-b",
        home=home,
        user="a321",
        timeout=60,
        dry_run=True,
    )
    assert not result.peers[0].ok
    assert "SSH login failed" in result.peers[0].message
    assert exit_code_for_sync(result) == 1


def test_sync_home_ditto_dry_run_ok(fake_ctx, tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()
    (home / "note.txt").write_text("local", encoding="utf-8")
    fake_ctx.runner = RecordingRunner(fail_ssh=False)
    result = sync_home(
        fake_ctx,
        peer="node-b",
        home=home,
        remote_home=str(home),
        user="a321",
        timeout=60,
        dry_run=True,
    )
    assert result.strategy.startswith("newest-wins")
    assert "ditto" in result.strategy
    assert result.peers[0].ok
    assert exit_code_for_sync(result) == 0
    # remote inv empty → all local files planned for push
    assert (
        "push dry-run" in result.peers[0].push_stdout or "push dry-run" in result.peers[0].message
    )


def test_sync_home_with_progress_object(fake_ctx, tmp_path: Path):
    import io

    from maccluster.render.progress import SyncProgress

    home = tmp_path / "home"
    home.mkdir()
    (home / "note.txt").write_text("local", encoding="utf-8")
    fake_ctx.runner = RecordingRunner(fail_ssh=False)
    buf = io.StringIO()
    prog = SyncProgress(enabled=True, stream=buf, force=True, min_interval_s=0)
    result = sync_home(
        fake_ctx,
        peer="node-b",
        home=home,
        user="a321",
        timeout=60,
        dry_run=True,
        progress=prog,
    )
    assert result.peers[0].ok
    out = buf.getvalue()
    assert "plan:" in out or "inventory" in out or "%" in out


def test_sync_home_unknown_peer(fake_ctx, tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()
    fake_ctx.runner = RecordingRunner()
    with pytest.raises(CliError) as ei:
        sync_home(fake_ctx, peer="nope", home=home, user="a321", timeout=60)
    assert ei.value.exit_code == 2


def test_sync_home_push_pull_mutex(fake_ctx, tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()
    fake_ctx.runner = RecordingRunner()
    with pytest.raises(CliError) as ei:
        sync_home(
            fake_ctx,
            push_only=True,
            pull_only=True,
            home=home,
            user="a321",
            timeout=60,
        )
    assert ei.value.exit_code == 2


def test_ditto_allowlisted(tmp_path):
    """ditto is on the ProcessRunner allowlist (macOS archive tool).

    On Linux CI there is no /usr/bin/ditto — only check allowlist membership
    and that resolve() finds a stub when present on the search path.
    """
    from maccluster.adapters.process import ProcessRunner
    from maccluster.constants import ALLOWLIST_BASENAMES
    from maccluster.errors import CliError

    assert "ditto" in ALLOWLIST_BASENAMES

    stub = tmp_path / "ditto"
    stub.write_text("#!/bin/sh\nexit 0\n")
    stub.chmod(0o755)

    runner = ProcessRunner(search_paths=(str(tmp_path),))
    path = runner.resolve("ditto")
    assert path == str(stub)

    # Non-allowlisted basename still refused
    try:
        ProcessRunner(search_paths=(str(tmp_path),)).resolve("not-a-tool")
        raise AssertionError("expected CliError")
    except CliError as e:
        assert e.exit_code == 1
