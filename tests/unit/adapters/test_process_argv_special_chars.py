"""A-044: subprocess argv is shell=False; special chars stay literal."""

from __future__ import annotations

from maccluster.adapters.process import ProcessRunner


def test_semicolon_hostname_not_shell_expanded():
    runner = ProcessRunner()
    # If shell=True were used, ";echo pwned" could execute. With argv-only,
    # ping treats the whole string as a hostname and fails to resolve.
    result = runner.run(
        ["ping", "-c", "1", "-W", "1000", "127.0.0.1;echo pwned"],
        timeout=3,
    )
    assert any("127.0.0.1;echo pwned" == a for a in result.argv)
    assert result.returncode != 0
    combined = (result.stdout or "") + (result.stderr or "")
    # Shell expansion would typically leave a bare "pwned" line; argv path keeps it in the host string.
    # macOS: "cannot resolve" / "Unknown host"; Linux: "Name or service not known"
        low = combined.lower()
        assert (
            "cannot resolve" in low
            or "unknown host" in low
            or "name or service not known" in low
            or "temporary failure" in low
        )
