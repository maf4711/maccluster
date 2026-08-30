"""Wi-Fi top-N recent git repos for `maccluster sync dev`."""

from __future__ import annotations

import os
from collections.abc import Sequence
from ipaddress import IPv4Address
from pathlib import Path

import pytest

from maccluster.domain.models import Node, SyncHomeResult, SyncPeerResult
from maccluster.errors import CliError
from maccluster.ports.process import ProcessResult
from maccluster.services.sync_wifi import (
    intersect_repos_with_includes,
    list_recent_repos,
    merge_sync_results,
    wifi_hostname,
    wifi_ssh_target,
)


class FakeSyncRunner:
    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []

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
        if name in ("ssh", "scp", "ditto"):
            return ProcessResult(argv=full, returncode=0, stdout="", stderr="")
        return ProcessResult(argv=full, returncode=1, stdout="", stderr="unknown")


def _set_mtime(path: Path, ns: int) -> None:
    os.utime(path, ns=(ns, ns))


def _git_repo(root: Path, name: str, activity_ns: int) -> Path:
    repo = root / name
    git = repo / ".git"
    git.mkdir(parents=True)
    (repo / "readme").write_text("x\n", encoding="utf-8")
    head = git / "HEAD"
    head.write_text("ref: refs/heads/main\n", encoding="utf-8")
    index = git / "index"
    index.write_bytes(b"DIRC")
    _set_mtime(head, activity_ns)
    _set_mtime(index, activity_ns)
    _set_mtime(git, activity_ns)
    _set_mtime(repo, activity_ns)
    return repo


def test_list_recent_repos_ranks_by_git_activity(tmp_path: Path):
    _git_repo(tmp_path, "alpha", 1_000)
    _git_repo(tmp_path, "beta", 3_000)
    _git_repo(tmp_path, "gamma", 2_000)
    (tmp_path / "not-a-repo").mkdir()
    (tmp_path / "not-a-repo" / "file.txt").write_text("no git\n", encoding="utf-8")
    nested = tmp_path / "plain" / "nested"
    nested.mkdir(parents=True)
    (nested / ".git").mkdir()
    (nested / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")

    assert list_recent_repos(tmp_path, limit=2) == ("beta", "gamma")
    all_names = list_recent_repos(tmp_path, limit=10)
    assert all_names[:3] == ("beta", "gamma", "alpha")
    assert "not-a-repo" not in all_names
    assert "plain" not in all_names
    assert "nested" not in all_names


def test_list_recent_repos_counts_gitfile_worktree(tmp_path: Path):
    wt = tmp_path / "worktree"
    wt.mkdir()
    gitfile = wt / ".git"
    gitfile.write_text("gitdir: /tmp/somewhere.git\n", encoding="utf-8")
    _set_mtime(gitfile, 9_000)
    _set_mtime(wt, 9_000)
    _git_repo(tmp_path, "older", 100)
    assert list_recent_repos(tmp_path, limit=1) == ("worktree",)


def test_list_recent_repos_limit_zero_and_missing(tmp_path: Path):
    _git_repo(tmp_path, "one", 1)
    assert list_recent_repos(tmp_path, limit=0) == ()
    assert list_recent_repos(tmp_path, limit=-3) == ()
    assert list_recent_repos(tmp_path / "missing", limit=10) == ()


def test_list_recent_repos_skips_dot_dirs(tmp_path: Path):
    hidden = tmp_path / ".hidden"
    hidden.mkdir()
    (hidden / ".git").mkdir()
    (hidden / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    _git_repo(tmp_path, "visible", 50)
    assert list_recent_repos(tmp_path, limit=10) == ("visible",)


def test_wifi_hostname_prefers_bonjour_local():
    node = Node(
        id="node-mini",
        hostnames=(
            "CM-CFMQ2D029F",
            "CM-CFMQ2D029F.local",
            "cm-cfmq2d029f",
        ),
        ip=IPv4Address("10.42.0.1"),
        hw_uuid="u",
    )
    assert wifi_hostname(node) == "CM-CFMQ2D029F.local"
    bare = Node(
        id="bare",
        hostnames=("only-name",),
        ip=IPv4Address("10.42.0.2"),
        hw_uuid="u",
    )
    assert wifi_hostname(bare) is None


def test_wifi_hostname_skips_a_local_name_that_does_not_resolve():
    """macOS appends -2/-2930 on a name collision and `config refresh` keeps the
    old name in the list, so entry #1 is routinely stale while a working alias
    sits further down (node-a: CM-CFMQ2D029F.local dead, -2.local live)."""
    node = Node(
        id="node-a",
        hostnames=(
            "CM-CFMQ2D029F",
            "CM-CFMQ2D029F.local",
            "CM-CFMQ2D029F-2",
            "CM-CFMQ2D029F-2.local",
        ),
        ip=IPv4Address("10.42.0.1"),
        hw_uuid="u",
    )
    resolves = {"cm-cfmq2d029f-2.local"}
    assert wifi_hostname(node, resolves=lambda h: h.lower() in resolves) == "CM-CFMQ2D029F-2.local"


def test_wifi_hostname_falls_back_to_first_when_none_resolve():
    """A resolver that answers no is not proof of a dead host — mDNS is flaky and
    the peer may be asleep. Keep the old behaviour so ssh reports the real error."""
    node = Node(
        id="node-a",
        hostnames=("a.local", "b.local"),
        ip=IPv4Address("10.42.0.1"),
        hw_uuid="u",
    )
    assert wifi_hostname(node, resolves=lambda _h: False) == "a.local"


def test_wifi_ssh_target_prefers_the_resolvable_alias():
    node = Node(
        id="node-a",
        hostnames=("dead.local", "live.local"),
        ip=IPv4Address("10.42.0.1"),
        hw_uuid="u",
        ssh_target="a321@10.42.0.1",
    )
    target = wifi_ssh_target(node, default_user="fallback", resolves=lambda h: h == "live.local")
    assert target == "a321@live.local"


def test_wifi_ssh_target_uses_local_not_tb_ip():
    node = Node(
        id="node-b",
        hostnames=("mac-mini-b.local", "mac-mini-b"),
        ip=IPv4Address("10.42.0.2"),
        hw_uuid="u",
        ssh_target="bob@10.42.0.2",
    )
    assert wifi_ssh_target(node, default_user="alice") == "bob@mac-mini-b.local"
    no_host = Node(
        id="x",
        hostnames=("nope",),
        ip=IPv4Address("10.42.0.9"),
        hw_uuid="u",
        ssh_target="bob@10.42.0.9",
    )
    assert wifi_ssh_target(no_host, default_user="alice") is None


def test_intersect_repos_with_user_includes():
    repos = ("maccluster", "fabrik", "old-tool")
    assert intersect_repos_with_includes(repos, ()) == repos
    assert intersect_repos_with_includes(repos, ("maccluster/", "fabrik/src")) == (
        "maccluster",
        "fabrik",
    )
    assert intersect_repos_with_includes(repos, ("does-not-exist/",)) == ()


def _peer(*, via: str, ok: bool = True) -> SyncPeerResult:
    return SyncPeerResult(
        peer_id="node-b",
        peer_ip="10.42.0.2",
        ssh_target="a321@10.42.0.2" if via == "tb" else "a321@mac-mini-b.local",
        push_rc=0,
        pull_rc=0,
        ok=ok,
        via=via,
        message="ok",
    )


def test_merge_sync_results_concatenates_peers_and_wifi_repos():
    tb = SyncHomeResult(
        local_home="/tmp/Developer",
        dry_run=True,
        strategy="newer (Apple ditto)",
        peers=(_peer(via="tb"),),
        target="dev",
        includes=(),
    )
    wifi = SyncHomeResult(
        local_home="/tmp/Developer",
        dry_run=True,
        strategy="newer (Apple ditto)",
        peers=(_peer(via="wifi"),),
        target="dev",
        includes=("maccluster", "fabrik"),
        wifi_repos=("maccluster", "fabrik"),
    )
    merged = merge_sync_results(tb, wifi)
    assert len(merged.peers) == 2
    assert [p.via for p in merged.peers] == ["tb", "wifi"]
    assert merged.wifi_repos == ("maccluster", "fabrik")
    assert "wifi" in merged.strategy


def test_sync_home_wifi_binds_no_bridge_and_uses_local(fake_ctx, tmp_path: Path):
    from maccluster.services.sync_service import sync_home

    tree = tmp_path / "Developer"
    tree.mkdir()
    (tree / "repo").mkdir()
    (tree / "repo" / "a.py").write_text("print(1)\n", encoding="utf-8")
    fake_ctx.runner = FakeSyncRunner()
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
        via="wifi",
        includes=("repo",),
    )
    assert result.peers[0].ok
    assert result.peers[0].via == "wifi"
    assert result.peers[0].ssh_target == "a321@mac-mini-b.local"
    ssh_calls = [c for c in fake_ctx.runner.calls if Path(c[0]).name == "ssh"]
    assert ssh_calls
    joined = " ".join(" ".join(c) for c in ssh_calls)
    assert "BindAddress=" not in joined
    assert "mac-mini-b.local" in joined
    assert "10.42.0.2" not in joined


def test_sync_cmd_dev_runs_tb_then_wifi(fake_ctx, tmp_path: Path, monkeypatch, capsys):
    from maccluster.cli.parser import build_parser
    from maccluster.commands import sync_cmd

    monkeypatch.setattr(
        "maccluster.services.sync_history.write_run_log",
        lambda result, log_dir=None: tmp_path / "sync-log.json",
    )
    tree = tmp_path / "Developer"
    _git_repo(tree, "fresh", 5_000)
    fake_ctx.runner = FakeSyncRunner()
    args = build_parser().parse_args(
        [
            "sync",
            "dev",
            "--home",
            str(tree),
            "--peer",
            "node-b",
            "--dry-run",
            "--no-speedtest",
            "--no-progress",
            "--no-mcprt",
            "--user",
            "a321",
        ]
    )
    code = sync_cmd.run(fake_ctx, args)
    assert code == 0
    out = capsys.readouterr().out
    assert "[tb]" in out
    assert "[wifi]" in out
    assert "wifi_repos=fresh" in out
    ssh_calls = [c for c in fake_ctx.runner.calls if Path(c[0]).name == "ssh"]
    joined = " ".join(" ".join(c) for c in ssh_calls)
    assert "BindAddress=" in joined
    assert "mac-mini-b.local" in joined


def test_sync_cmd_dev_no_wifi_skips_local_ssh(fake_ctx, tmp_path: Path, monkeypatch, capsys):
    from maccluster.cli.parser import build_parser
    from maccluster.commands import sync_cmd

    monkeypatch.setattr(
        "maccluster.services.sync_history.write_run_log",
        lambda result, log_dir=None: tmp_path / "sync-log.json",
    )
    tree = tmp_path / "Developer"
    _git_repo(tree, "fresh", 5_000)
    fake_ctx.runner = FakeSyncRunner()
    args = build_parser().parse_args(
        [
            "sync",
            "dev",
            "--home",
            str(tree),
            "--peer",
            "node-b",
            "--dry-run",
            "--no-speedtest",
            "--no-progress",
            "--no-wifi",
            "--no-mcprt",
            "--user",
            "a321",
        ]
    )
    code = sync_cmd.run(fake_ctx, args)
    assert code == 0
    out = capsys.readouterr().out
    assert "[wifi]" not in out
    assert "wifi_repos=" not in out
    joined = " ".join(" ".join(c) for c in fake_ctx.runner.calls)
    assert "mac-mini-b.local" not in joined


def test_sync_home_wifi_errors_without_local_hostname(fake_ctx, tmp_path: Path):
    from maccluster.services.sync_service import sync_home

    tree = tmp_path / "Developer"
    tree.mkdir()
    cfg_path = fake_ctx.config_path
    text = cfg_path.read_text(encoding="utf-8")
    cfg_path.write_text(
        text.replace("mac-mini-b.local", "mac-mini-b-only"),
        encoding="utf-8",
    )
    fake_ctx.runner = FakeSyncRunner()
    with pytest.raises(CliError) as exc:
        sync_home(
            fake_ctx,
            peer="node-b",
            home=tree,
            user="a321",
            timeout=60,
            dry_run=True,
            no_speedtest=True,
            write_log=False,
            target="dev",
            via="wifi",
        )
    assert exc.value.exit_code == 1
    assert ".local" in exc.value.message
