"""Bandwidth bench service."""

from __future__ import annotations

from ipaddress import ip_address

from maccluster.app_factory import AppContext
from maccluster.domain.models import BenchResult
from maccluster.errors import CliError
from maccluster.services.config_service import load_and_bind_self


def run_bench(ctx: AppContext, target: str | None, *, duration: int = 5) -> BenchResult:
    if not target:
        raise CliError("bench requires a target IP or node id", exit_code=2)
    if ctx.bench is None:
        raise CliError(
            "iperf3 not found — install via Homebrew: brew install iperf3",
            exit_code=1,
        )
    if not ctx.bench.available():
        raise CliError(
            "iperf3 not found — install via Homebrew: brew install iperf3",
            exit_code=1,
        )

    resolved = target
    bind_ip = None
    # Allow node id from config; bind iperf to TB Self-IP
    try:
        cfg, self_node = load_and_bind_self(ctx)
        bind_ip = str(self_node.ip)
        for n in cfg.nodes:
            if n.id == target:
                if n.id == self_node.id:
                    raise CliError("cannot bench self node", exit_code=2)
                resolved = str(n.ip)
                break
        else:
            # validate as IP
            try:
                ip_address(target)
            except ValueError as exc:
                # maybe still a hostname
                if not all(c.isalnum() or c in ".-_" for c in target):
                    raise CliError(f"invalid bench target: {target!r}", exit_code=2) from exc
    except CliError:
        raise
    except Exception:
        try:
            ip_address(target)
        except ValueError as exc:
            raise CliError(f"invalid bench target: {target!r}", exit_code=2) from exc

    result = ctx.bench.run(resolved, duration=duration, bind_ip=bind_ip)
    if not result.success:
        raise CliError(result.message, exit_code=1)
    return result
