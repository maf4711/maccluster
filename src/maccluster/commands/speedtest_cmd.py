"""speedtest — TB cable grade + iperf3 over bridge."""

from __future__ import annotations

from maccluster.app_factory import AppContext
from maccluster.render.json_out import dumps, to_jsonable
from maccluster.services.speedtest_service import format_speedtest_report, run_speedtest


def run(ctx: AppContext, args) -> int:
    report = run_speedtest(
        ctx,
        peer=getattr(args, "peer", None),
        duration=int(getattr(args, "duration", 5) or 5),
        skip_iperf=bool(getattr(args, "cable_only", False)),
        try_start_server=not bool(getattr(args, "no_server", False)),
    )
    if ctx.json_mode:
        print(dumps("speedtest", to_jsonable(report)))
    else:
        print(format_speedtest_report(report))
    # exit 0 if cable good enough (iperf optional); 3 if cable weak
    if report.good_enough:
        return 0
    return 3
