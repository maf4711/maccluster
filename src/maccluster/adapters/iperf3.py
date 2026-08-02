"""Optional iperf3 bandwidth bench adapter."""

from __future__ import annotations

import json
import re
from ipaddress import ip_address

from maccluster.constants import TIMEOUT_IPERF
from maccluster.domain.models import BenchResult
from maccluster.errors import CliError
from maccluster.ports.process import ProcessRunnerPort


class Iperf3Bench:
    def __init__(self, runner: ProcessRunnerPort) -> None:
        self._runner = runner

    def available(self) -> bool:
        try:
            self._runner.resolve("iperf3")
            return True
        except CliError:
            return False

    def run(
        self,
        target: str,
        *,
        duration: int = 5,
        bind_ip: str | None = None,
    ) -> BenchResult:
        if not self.available():
            return BenchResult(
                target=target,
                mbps=None,
                success=False,
                message="iperf3 not found — install via Homebrew: brew install iperf3",
            )
        # Validate target is IP or simple hostname
        try:
            ip_address(target)
        except ValueError:
            if not re.fullmatch(r"[A-Za-z0-9._-]+", target):
                raise CliError(f"invalid bench target: {target!r}", exit_code=2) from None
        duration = max(1, min(int(duration), 60))
        abs_iperf = self._runner.resolve("iperf3")
        argv = [abs_iperf, "-c", target, "-t", str(duration), "-J"]
        # Force traffic out the TB bridge Self-IP (not Wi‑Fi)
        if bind_ip:
            argv.extend(["-B", str(bind_ip)])
        result = self._runner.run(
            argv,
            timeout=TIMEOUT_IPERF + duration,
        )
        if result.returncode != 0:
            return BenchResult(
                target=target,
                mbps=None,
                success=False,
                message=(result.stderr or result.stdout or "iperf3 failed")[:300],
            )
        mbps = _parse_iperf_json(result.stdout)
        return BenchResult(
            target=target,
            mbps=mbps,
            success=mbps is not None,
            message="ok" if mbps is not None else "could not parse iperf3 output",
        )


def _parse_iperf_json(text: str) -> float | None:
    try:
        data = json.loads(text)
        # bits_per_second in end.sum_sent or end.sum_received
        end = data.get("end") or {}
        for key in ("sum_sent", "sum_received", "sum"):
            block = end.get(key)
            if isinstance(block, dict) and "bits_per_second" in block:
                return float(block["bits_per_second"]) / 1_000_000.0
    except (json.JSONDecodeError, TypeError, ValueError):
        pass
    m = re.search(r"([\d.]+)\s*Mbits/sec", text)
    if m:
        return float(m.group(1))
    return None


class FakeBench:
    def __init__(self, *, available: bool = True, mbps: float = 1000.0) -> None:
        self._available = available
        self._mbps = mbps

    def available(self) -> bool:
        return self._available

    def run(self, target: str, *, duration: int = 5, bind_ip: str | None = None) -> BenchResult:
        if not self._available:
            return BenchResult(
                target=target,
                mbps=None,
                success=False,
                message="iperf3 not found — install via Homebrew: brew install iperf3",
            )
        return BenchResult(target=target, mbps=self._mbps, success=True, message="ok")
