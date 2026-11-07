"""`--no-progress` silences the live bar, not the run.

A dry run that prints nothing and logs nothing is indistinguishable from a
successful no-op, so the summary (peers, planned files/bytes per direction,
conflict stats, transport) and the JSON run log must survive the flag.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from maccluster.cli.parser import build_parser
from maccluster.commands import sync_cmd
from maccluster.errors import CliError
from maccluster.ports.process import ProcessResult


class StubRunner:
    """ssh/scp/ditto all succeed; the peer reports an empty inventory."""

    def resolve(self, basename: str) -> str:
        if basename in ("ssh", "scp", "ditto"):
            return f"/usr/bin/{basename}"
        raise CliError(f"tool not found: {basename}", exit_code=1)

    def run(
        self, argv: Sequence[str], *, timeout: float = 15.0, check: bool = False
    ) -> ProcessResult:
        full = tuple(argv)
        name = Path(full[0]).name
        if name in ("ssh", "scp", "ditto"):
            return ProcessResult(argv=full, returncode=0, stdout="", stderr="")
        return ProcessResult(argv=full, returncode=1, stdout="", stderr="unknown")


def _args(home: Path, *extra: str):
    return build_parser().parse_args(
        [
            "sync",
            "home",
            "--dry-run",
            "--no-speedtest",
            "--peer",
            "node-b",
            "--user",
            "a321",
            "--home",
            str(home),
            "--remote-home",
            str(home),
            "--include",
            "Documents/",
            *extra,
        ]
    )


def _home(tmp_path: Path) -> Path:
    home = tmp_path / "home"
    (home / "Documents").mkdir(parents=True)
    (home / "Documents" / "note.txt").write_text("local", encoding="utf-8")
    return home


def _without_log_line(text: str) -> list[str]:
    return [ln for ln in text.splitlines() if not ln.startswith("log=")]


def test_no_progress_still_prints_summary_and_writes_log(fake_ctx, tmp_path: Path, capsys):
    home = _home(tmp_path)
    fake_ctx.runner = StubRunner()
    code = sync_cmd.run(fake_ctx, _args(home, "--no-progress"))
    out = capsys.readouterr().out
    assert code == 0
    assert out.strip(), "--no-progress must not silence the summary"
    # peers + planned files/bytes per direction + conflict stats + transport
    assert "node-b" in out
    assert "push=" in out and "pull=" in out
    assert "only_local=" in out and "skip_conflict=" in out
    assert "transport=" in out
    # …and the run log is still written
    log_dir = home / "Library" / "Logs" / "maccluster"
    assert sorted(log_dir.glob("sync-*.json")), "--no-progress must not skip the run log"
    assert f"log={log_dir}" in out


def test_no_progress_matches_the_progress_summary(fake_ctx, tmp_path: Path, capsys):
    """Same run, same stdout summary — only the stderr bar differs."""
    home = _home(tmp_path)
    fake_ctx.runner = StubRunner()
    sync_cmd.run(fake_ctx, _args(home, "--no-progress"))
    quiet = capsys.readouterr().out
    sync_cmd.run(fake_ctx, _args(home))
    loud = capsys.readouterr().out
    assert _without_log_line(quiet) == _without_log_line(loud)
