"""bench command."""

from __future__ import annotations

from maccluster.app_factory import AppContext
from maccluster.cli.exit_codes import OK
from maccluster.render.json_out import dumps, to_jsonable
from maccluster.services.bench_service import run_bench


def run(ctx: AppContext, args) -> int:
    target = getattr(args, "target", None)
    duration = int(getattr(args, "duration", 5) or 5)
    result = run_bench(ctx, target, duration=duration)
    if ctx.json_mode:
        print(dumps("bench", to_jsonable(result)))
    else:
        bits = [f"target={result.target} throughput={result.mbps:.2f} Mbit/s"]
        bits.append(f"quality={result.quality.value}")
        if result.retransmits is not None:
            bits.append(f"retransmits={result.retransmits}")
        if result.flags:
            bits.append("flags=" + ",".join(result.flags))
        print(" ".join(bits))
    return OK
