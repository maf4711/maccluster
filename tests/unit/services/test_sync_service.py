"""Home sync service (Apple ditto, newest-wins)."""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

import pytest

from maccluster.errors import CliError
from maccluster.ports.process import ProcessResult
from maccluster.services.sync_filters import merge_includes, resolve_presets
from maccluster.services.sync_service import (
    _REMOTE_INVENTORY_PY,
    FileMeta,
    apply_batch_limits,
    exit_code_for_sync,
    inventory_local,
    is_excluded,
    log_home_for_target,
    parse_inventory_text,
    plan_transfers,
    resolve_sync_tree,
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
    push, pull, stats = plan_transfers(local, remote)
    assert "a.txt" in push and "only-local.txt" in push
    assert "b.txt" not in push
    assert "b.txt" in pull and "only-remote.txt" in pull
    assert "equal.txt" not in push and "equal.txt" not in pull
    assert stats["equal"] == 1
    assert stats["only_local"] == 1
    assert stats["only_remote"] == 1


def test_plan_transfers_prefer_local_and_skip():
    local = {"x": FileMeta(mtime_ns=1, size=10), "y": FileMeta(mtime_ns=1, size=1)}
    remote = {"x": FileMeta(mtime_ns=9, size=1), "y": FileMeta(mtime_ns=1, size=1)}
    push, pull, _ = plan_transfers(local, remote, policy="prefer-local")
    assert "x" in push and not pull
    push2, pull2, st = plan_transfers(local, remote, policy="skip-conflict")
    assert "x" not in push2 and "x" not in pull2
    assert st["conflicts_skipped"] >= 1


def test_plan_transfers_larger():
    local = {"a": FileMeta(mtime_ns=1, size=100)}
    remote = {"a": FileMeta(mtime_ns=9, size=10)}
    push, pull, _ = plan_transfers(local, remote, policy="larger")
    assert "a" in push and not pull


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
        no_speedtest=True,
        write_log=False,
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
        no_speedtest=True,
        write_log=False,
    )
    assert "ditto" in result.strategy or result.conflict_policy == "newer"
    assert result.peers[0].ok
    assert exit_code_for_sync(result) == 0
    # remote inv empty → all local files planned for push
    assert (
        "push dry-run" in result.peers[0].push_stdout
        or "push dry-run" in result.peers[0].message
        or result.peers[0].push_files >= 1
    )


def test_presets_and_batch_limits():
    assert "Documents/" in resolve_presets(["documents"])
    inc = merge_includes(["desktop"], ["Projects/"])
    assert "Desktop/" in inc and "Projects/" in inc
    push = ["a", "b", "c"]
    pull = ["d"]
    sizes = {"a": 10, "b": 20, "c": 30, "d": 5}
    p, q, trunc = apply_batch_limits(push, pull, sizes, sizes, max_files=2, max_bytes=None)
    assert len(p) + len(q) == 2
    assert trunc


def test_resolve_sync_tree_explicit_overrides_action(tmp_path: Path):
    custom = tmp_path / "custom-dev"
    custom.mkdir()
    assert resolve_sync_tree("dev", custom) == custom
    assert resolve_sync_tree("home", custom) == custom
    assert resolve_sync_tree("dev", str(custom)) == custom


def test_resolve_sync_tree_defaults(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    assert resolve_sync_tree("home", None) == tmp_path
    assert resolve_sync_tree("dev", None) == tmp_path / "Developer"
    assert resolve_sync_tree("developer", None) == tmp_path / "Developer"


def test_log_home_for_target_dev_stays_on_real_home(tmp_path: Path):
    tree = tmp_path / "Developer"
    tree.mkdir()
    assert log_home_for_target("home", tree) == tree
    assert log_home_for_target("dev", tree) == Path.home()
    assert log_home_for_target("developer", tree) == Path.home()


def test_sync_dev_missing_tree_message(fake_ctx, tmp_path: Path):
    missing = tmp_path / "no-such-Developer"
    fake_ctx.runner = RecordingRunner(fail_ssh=False)
    with pytest.raises(CliError) as exc:
        sync_home(
            fake_ctx,
            peer="node-b",
            home=missing,
            user="a321",
            timeout=60,
            dry_run=True,
            no_speedtest=True,
            write_log=False,
            target="dev",
        )
    assert exc.value.exit_code == 1
    assert "Developer" in exc.value.message
    assert str(missing) in exc.value.message


def test_sync_dev_dry_run_uses_developer_tree(fake_ctx, tmp_path: Path):
    tree = tmp_path / "Developer"
    tree.mkdir()
    (tree / "repo").mkdir()
    (tree / "repo" / "main.py").write_text("print(1)\n", encoding="utf-8")
    fake_ctx.runner = RecordingRunner(fail_ssh=False)
    result = sync_home(
        fake_ctx,
        peer="node-b",
        home=tree,
        remote_home=str(tree),
        user="a321",
        timeout=60,
        dry_run=True,
        no_speedtest=True,
        write_log=False,
        target="dev",
    )
    assert result.target == "dev"
    assert result.local_home == str(tree)
    assert result.peers[0].ok
    assert result.peers[0].push_files >= 1
    assert "**/.build/" in result.excludes


def test_remote_inventory_script_includes_git_when_dotdirs(tmp_path: Path):
    tree = tmp_path / "dev"
    git = tree / "repo" / ".git"
    git.mkdir(parents=True)
    (git / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (tree / "repo" / "a.py").write_text("x\n", encoding="utf-8")
    venv = tree / "repo" / ".venv" / "lib"
    venv.mkdir(parents=True)
    (venv / "x.py").write_text("z\n", encoding="utf-8")
    script = tmp_path / "inv.py"
    script.write_text(_REMOTE_INVENTORY_PY, encoding="utf-8")
    excl = tmp_path / "ex.txt"
    excl.write_text("**/.venv/\n**/node_modules/\n", encoding="utf-8")

    env = {**os.environ, "MACCLUSTER_INV_DOTDIRS": "1"}
    r = subprocess.run(
        [sys.executable, str(script), str(tree), str(excl)],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert r.returncode == 0, r.stderr
    inv = parse_inventory_text(r.stdout)
    assert "repo/a.py" in inv
    assert "repo/.git/HEAD" in inv
    assert not any(".venv" in k for k in inv)

    r2 = subprocess.run(
        [sys.executable, str(script), str(tree), str(excl)],
        capture_output=True,
        text=True,
        check=False,
    )
    inv2 = parse_inventory_text(r2.stdout)
    assert "repo/a.py" in inv2
    assert "repo/.git/HEAD" not in inv2


def test_sync_home_compare(fake_ctx, tmp_path: Path):
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
        compare_only=True,
        no_speedtest=True,
        write_log=False,
    )
    assert result.compare_only
    assert result.peers[0].ok
    assert result.peers[0].only_local >= 1


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


def test_plan_transfers_partial_remote_does_not_push_unlisted():
    """A truncated remote inventory must not be read as "file absent on peer".

    The remote walk runs under a time budget. When it trips, the inventory
    covers only part of the tree. Treating everything it never reached as
    only_local turns a delta sync into a full re-copy: observed in the field
    as 83.5 GB pushed for a delta of a few hundred MB, because the walk had
    listed 40050 of 4384872 files (0.9%) before the budget stopped it.
    """
    local = {
        "seen-newer.txt": FileMeta(mtime_ns=200, size=1),
        "seen-equal.txt": FileMeta(mtime_ns=50, size=1),
        "never-listed.txt": FileMeta(mtime_ns=1, size=1),
    }
    remote = {
        "seen-newer.txt": FileMeta(mtime_ns=100, size=1),
        "seen-equal.txt": FileMeta(mtime_ns=50, size=1),
    }
    push, _pull, stats = plan_transfers(local, remote, remote_complete=False)
    # A positively observed difference still moves.
    assert "seen-newer.txt" in push
    # A file the walk never reached is unknown, not absent.
    assert "never-listed.txt" not in push
    assert stats["remote_unknown"] == 1
    assert stats["only_local"] == 0


def test_plan_transfers_complete_remote_still_pushes_only_local():
    """With a complete inventory, absent really does mean absent."""
    local = {"only-local.txt": FileMeta(mtime_ns=1, size=1)}
    push, _pull, stats = plan_transfers(local, {}, remote_complete=True)
    assert "only-local.txt" in push
    assert stats["only_local"] == 1
    assert stats["remote_unknown"] == 0
