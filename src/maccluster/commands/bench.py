"""bench command."""

from __future__ import annotations

from maccluster.app_factory import AppContext
from maccluster.cli.exit_codes import OK
from maccluster.render.json_out import dumps, to_jsonable
from maccluster.render.plain import render_mesh_bench
from maccluster.services.bench_service import run_bench
from maccluster.services.mesh_bench_service import (
    exit_for_mesh_report,
    reject_mesh_target_combo,
    run_mesh_bench,
)


def run(ctx: AppContext, args) -> int:
    mesh = bool(getattr(args, "mesh", False))
    target = getattr(args, "target", None)
    duration = int(getattr(args, "duration", 5) or 5)
    if mesh:
        reject_mesh_target_combo(mesh=True, target=target)
        report = run_mesh_bench(
            ctx,
            duration=duration,
            peer=getattr(args, "peer", None),
            force=bool(getattr(args, "force", False)),
        )
        if ctx.json_mode:
            print(dumps("bench", to_jsonable(report)))
        else:
            print(render_mesh_bench(report))
        return exit_for_mesh_report(report)
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
