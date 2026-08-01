"""Optional SSH peer probe (BatchMode, short timeout)."""

from __future__ import annotations

from maccluster.domain.enums import ReachabilityState
from maccluster.ports.process import ProcessRunnerPort
from maccluster.ports.reachability import ReachabilityResult


def ssh_probe(
    runner: ProcessRunnerPort,
    target: str,
    *,
    timeout: float = 3.0,
) -> ReachabilityResult:
    """Non-interactive SSH probe; no password prompts."""
    try:
        abs_ssh = runner.resolve("ssh")
    except Exception as exc:
        return ReachabilityResult(
            target=target,
            state=ReachabilityState.UNKNOWN,
            method="ssh",
            detail=str(exc),
        )
    argv = [
        abs_ssh,
        "-o",
        "BatchMode=yes",
        "-o",
        f"ConnectTimeout={max(1, int(timeout))}",
        "-o",
        "PasswordAuthentication=no",
        "-o",
        "KbdInteractiveAuthentication=no",
        "-o",
        "StrictHostKeyChecking=accept-new",
        target,
        "true",
    ]
    result = runner.run(argv, timeout=timeout + 1.0)
    if result.returncode == 0:
        return ReachabilityResult(
            target=target,
            state=ReachabilityState.UP,
            method="ssh",
        )
    return ReachabilityResult(
        target=target,
        state=ReachabilityState.DOWN,
        method="ssh",
        detail=(result.stderr or result.stdout)[:200],
    )
