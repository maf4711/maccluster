"""Ping reachability uses source IP and TCP fallback helpers."""

from __future__ import annotations

from maccluster.adapters.ping_macos import FakeReachability, PingReachability
from maccluster.domain.enums import ReachabilityState
from maccluster.ports.process import ProcessResult


class _Runner:
    def __init__(self) -> None:
        self.last_argv: list[str] | None = None

    def run(self, argv, *, timeout=15.0, check=False):
        self.last_argv = list(argv)
        return ProcessResult(
            argv=list(argv),
            returncode=0,
            stdout="64 bytes time=1.2 ms\n",
            stderr="",
        )


def test_ping_passes_source():
    r = _Runner()
    pr = PingReachability(r)  # type: ignore[arg-type]
    out = pr.ping("10.42.0.2", source="10.42.0.1")
    assert out.state == ReachabilityState.UP
    assert r.last_argv is not None
    assert "-S" in r.last_argv
    assert "10.42.0.1" in r.last_argv


def test_fake_tcp_probe():
    fr = FakeReachability({"10.42.0.2": ReachabilityState.DOWN})
    fr.tcp_states["10.42.0.2"] = ReachabilityState.UP
    t = fr.tcp_probe("10.42.0.2")
    assert t.state == ReachabilityState.UP
    assert t.method.startswith("tcp:")
