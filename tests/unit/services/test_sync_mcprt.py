"""MCPRT preflight for `maccluster sync dev` (git ship + TestFlight)."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from maccluster.errors import CliError
from maccluster.ports.process import ProcessResult
from maccluster.services.sync_mcprt import (
    is_secret_rel,
    looks_like_ios_app,
    run_mcprt,
)


def test_is_secret_rel_skips_env_and_keys():
    assert is_secret_rel(".env")
    assert is_secret_rel("app/.env.local")
    assert is_secret_rel("AuthKey_ABC.p8")
    assert is_secret_rel("certs/foo.pem")
    assert is_secret_rel("secrets.json")
    assert not is_secret_rel("src/main.py")
    assert not is_secret_rel("README.md")


def test_looks_like_ios_app(tmp_path: Path):
    py = tmp_path / "lib"
    py.mkdir()
    (py / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    assert not looks_like_ios_app(py)

    ios = tmp_path / "app"
    ios.mkdir()
    (ios / "Foo.xcodeproj").mkdir()
    assert looks_like_ios_app(ios)

    nested = tmp_path / "fin"
    (nested / "ios").mkdir(parents=True)
    (nested / "ios" / "Bar.xcodeproj").mkdir()
    assert looks_like_ios_app(nested)


class GitishRunner:
    """Records git/gh/bash argv; returns canned porcelain / success."""

    def __init__(
        self,
        *,
        porcelain: str = "",
        branch: str = "main",
        cached: str = "",
        has_origin: bool = True,
        pr_json: str = "[]",
    ) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.porcelain = porcelain
        self.branch = branch
        self.cached = cached
        self.has_origin = has_origin
        self.pr_json = pr_json

    def resolve(self, basename: str) -> str:
        if basename in ("git", "gh", "bash", "ssh", "scp", "ditto"):
            return f"/usr/bin/{basename}"
        raise CliError(f"tool not found: {basename}", exit_code=1)

    def run(
        self,
        argv: Sequence[str],
        *,
        timeout: float = 15.0,
        check: bool = False,
    ) -> ProcessResult:
        full = tuple(str(x) for x in argv)
        self.calls.append(full)
        name = Path(full[0]).name
        args = full[1:]
        if name == "git":
            if "-C" in args:
                i = args.index("-C")
                args = args[i + 2 :]
            cmd = args[0] if args else ""
            if cmd == "status":
                return ProcessResult(full, 0, self.porcelain, "")
            if cmd == "branch":
                return ProcessResult(full, 0, self.branch + "\n", "")
            if cmd == "diff":
                return ProcessResult(full, 0, self.cached, "")
            if cmd == "remote":
                rc = 0 if self.has_origin else 1
                out = "git@github.com:maf4711/fresh.git\n" if self.has_origin else ""
                return ProcessResult(full, rc, out, "" if rc == 0 else "no origin")
            if cmd in ("add", "reset", "commit", "fetch", "merge", "push", "checkout"):
                return ProcessResult(full, 0, "ok\n", "")
            return ProcessResult(full, 0, "", "")
        if name == "gh":
            if "pr" in args and "list" in args:
                return ProcessResult(full, 0, self.pr_json, "")
            return ProcessResult(full, 0, "", "")
        if name == "bash":
            return ProcessResult(full, 0, "testflight ok\n", "")
        return ProcessResult(full, 1, "", "unknown")


def test_run_mcprt_dry_run_does_not_commit(tmp_path: Path, fake_ctx):
    repo = tmp_path / "fresh"
    repo.mkdir()
    (repo / ".git").mkdir()
    (repo / "a.py").write_text("x\n", encoding="utf-8")
    runner = GitishRunner(porcelain=" M a.py\n")
    fake_ctx.runner = runner
    result = run_mcprt(
        fake_ctx,
        (repo,),
        dry_run=True,
        testflight=True,
        timeout=60.0,
    )
    assert result.repos[0].name == "fresh"
    assert "would" in result.repos[0].message.lower() or result.repos[0].committed is False
    joined = " ".join(" ".join(c) for c in runner.calls)
    assert " commit " not in joined
    assert " push " not in joined
    assert "bash" not in joined


def test_run_mcprt_commits_then_merges_and_pushes(tmp_path: Path, fake_ctx):
    repo = tmp_path / "fresh"
    repo.mkdir()
    (repo / ".git").mkdir()
    runner = GitishRunner(porcelain=" M a.py\n", cached="a.py\n", has_origin=True)
    fake_ctx.runner = runner
    result = run_mcprt(
        fake_ctx,
        (repo,),
        dry_run=False,
        testflight=False,
        timeout=60.0,
    )
    assert result.ok
    assert result.repos[0].committed is True
    assert result.repos[0].pushed is True
    cmds = [" ".join(c) for c in runner.calls]
    assert any("commit" in c for c in cmds)
    assert any("merge" in c for c in cmds)
    assert any("push" in c for c in cmds)
    assert not any(Path(c[0]).name == "bash" for c in runner.calls)


def test_run_mcprt_testflight_for_ios_app(tmp_path: Path, fake_ctx, monkeypatch):
    repo = tmp_path / "meister-app"
    repo.mkdir()
    (repo / ".git").mkdir()
    (repo / "App.xcodeproj").mkdir()
    script = tmp_path / "ship.sh"
    script.write_text("#!/bin/bash\necho ok\n", encoding="utf-8")
    monkeypatch.setattr(
        "maccluster.services.sync_mcprt.testflight_ship_script",
        lambda _root: script,
    )
    runner = GitishRunner(porcelain="", has_origin=True)
    fake_ctx.runner = runner
    result = run_mcprt(
        fake_ctx,
        (repo,),
        dry_run=False,
        testflight=True,
        timeout=60.0,
    )
    assert result.repos[0].testflight == "ok"
    bash_calls = [c for c in runner.calls if Path(c[0]).name == "bash"]
    assert bash_calls
    joined = " ".join(bash_calls[0])
    assert str(script) in joined
    assert str(repo) in joined
    assert "intern" in joined
    assert "Extern" in joined


def test_sync_cmd_runs_mcprt_before_ditto(fake_ctx, tmp_path: Path, monkeypatch, capsys):
    from maccluster.cli.parser import build_parser
    from maccluster.commands import sync_cmd
    from maccluster.domain.models import (
        McprtRepoResult,
        McprtResult,
        SyncHomeResult,
        SyncPeerResult,
    )

    order: list[str] = []

    def fake_run_mcprt(*_a, **_k):
        order.append("mcprt")
        return McprtResult(
            repos=(McprtRepoResult(name="fresh", ok=True, message="ok"),),
            dry_run=True,
        )

    def fake_sync_home(*_a, **kwargs):
        order.append("ditto")
        return SyncHomeResult(
            local_home=str(tmp_path / "Developer"),
            dry_run=True,
            strategy="newer",
            peers=(
                SyncPeerResult(
                    peer_id="node-b",
                    peer_ip="10.42.0.2",
                    ssh_target="a@x",
                    push_rc=0,
                    pull_rc=0,
                    ok=True,
                    via=str(kwargs.get("via") or "tb"),
                ),
            ),
            target="dev",
        )

    monkeypatch.setattr("maccluster.commands.sync_cmd.run_mcprt", fake_run_mcprt)
    monkeypatch.setattr("maccluster.commands.sync_cmd.sync_home", fake_sync_home)

    tree = tmp_path / "Developer"
    repo = tree / "fresh"
    repo.mkdir(parents=True)
    (repo / ".git").mkdir()
    (repo / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
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
            "--user",
            "a321",
        ]
    )
    code = sync_cmd.run(fake_ctx, args)
    assert code == 0
    assert order[0] == "mcprt"
    assert "ditto" in order
    out = capsys.readouterr().out
    assert "mcprt" in out.lower()


def test_run_mcprt_unstages_secrets(tmp_path: Path, fake_ctx):
    repo = tmp_path / "fresh"
    repo.mkdir()
    (repo / ".git").mkdir()
    runner = GitishRunner(porcelain=" M .env\n M a.py\n", cached=".env\na.py\n")
    fake_ctx.runner = runner
    run_mcprt(fake_ctx, (repo,), dry_run=False, testflight=False, timeout=60.0)
    reset_calls = [c for c in runner.calls if "reset" in c]
    assert reset_calls
    assert any(".env" in c for c in reset_calls)
