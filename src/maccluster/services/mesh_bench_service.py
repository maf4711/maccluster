"""Full-mesh iperf3 over the Thunderbolt bridge (sequential, bound)."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from maccluster.adapters.iperf3 import _parse_iperf_json
from maccluster.app_factory import AppContext
from maccluster.cli.exit_codes import DEGRADED, ERROR, OK, USAGE
from maccluster.cluster_ssh import require_cluster_ip
from maccluster.constants import TB_TCP_FLOOR_MBPS
from maccluster.domain.enums import BenchQuality
from maccluster.domain.models import BenchResult, MeshBenchReport, MeshPathResult, Node
from maccluster.errors import CliError
from maccluster.health.bench_quality import assess_bench_quality
from maccluster.services.busy_guard import read_busy_state
from maccluster.services.config_service import load_and_bind_self
from maccluster.services.fleet_exec import directed_pairs, iter_peers, run_on_peer


def reject_mesh_target_combo(*, mesh: bool, target: str | None) -> None:
    if mesh and target:
        raise CliError(
            "bench --mesh does not take a positional target (use --peer)",
            exit_code=USAGE,
        )


def exit_for_mesh_report(report: MeshBenchReport) -> int:
    if report.busy_skipped:
        return DEGRADED
    if not report.paths:
        return USAGE
    if all(not p.ok for p in report.paths):
        return ERROR
    if any((not p.ok) or p.quality == BenchQuality.POOR for p in report.paths):
        return DEGRADED
    return OK


def remote_iperf_server_cmd(bind_ip: str) -> str:
    ip = str(require_cluster_ip(bind_ip))
    return (
        'export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"; '
        "command -v iperf3 >/dev/null || exit 66; "
        f"iperf3 -s -1 -D -B {ip}"
    )


def remote_iperf_client_cmd(*, dst_ip: str, src_ip: str, duration: int) -> str:
    dst = str(require_cluster_ip(dst_ip))
    src = str(require_cluster_ip(src_ip))
    dur = max(1, min(int(duration), 60))
    return (
        'export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"; '
        "command -v iperf3 >/dev/null || exit 66; "
        f"iperf3 -c {dst} -t {dur} -J -B {src} --connect-timeout 5000"
    )


def run_mesh_bench(
    ctx: AppContext,
    *,
    duration: int = 5,
    peer: str | None = None,
    force: bool = False,
    env: Mapping[str, str] | None = None,
    busy_path: Path | None = None,
) -> MeshBenchReport:
    if ctx.bench is None or not ctx.bench.available():
        raise CliError(
            "iperf3 not found — install via Homebrew: brew install iperf3",
            exit_code=ERROR,
        )
    cfg, self_node = load_and_bind_self(ctx)
    busy = read_busy_state(env=env, busy_path=busy_path)
    duration = max(1, min(int(duration), 60))
    if busy.busy and not force:
        return MeshBenchReport(
            bind_mode="tb-bridge",
            duration_s=duration,
            orchestrated=False,
            busy_skipped=True,
            paths=(),
            summary=f"fabric busy: {busy.reason} — skip saturation",
        )

    peers = iter_peers(cfg, self_node, peer=peer)
    if not peers:
        raise CliError("bench --mesh requires at least one peer", exit_code=USAGE)

    orchestrated = _can_orchestrate(ctx, self_ip=str(self_node.ip), sample=peers[0])
    pairs = directed_pairs(self_node, peers, orchestrated=orchestrated)
    paths = [
        _run_path(
            ctx,
            src=src,
            dst=dst,
            self_node=self_node,
            duration=duration,
            orchestrated=orchestrated,
        )
        for src, dst in pairs
    ]
    ok_n = sum(1 for p in paths if p.ok)
    note = "" if orchestrated else " orchestrate skipped: no ssh"
    return MeshBenchReport(
        bind_mode="tb-bridge",
        duration_s=duration,
        orchestrated=orchestrated,
        busy_skipped=False,
        paths=tuple(paths),
        summary=f"{ok_n}/{len(paths)} ok{note}",
    )


def _can_orchestrate(ctx: AppContext, *, self_ip: str, sample: Node) -> bool:
    try:
        ctx.runner.resolve("ssh")
    except CliError:
        return False
    hop = run_on_peer(
        ctx,
        self_ip=self_ip,
        node=sample,
        remote=("true",),
        timeout=4.0,
        connect_timeout=2,
    )
    return hop.ok


def _run_path(
    ctx: AppContext,
    *,
    src: Node,
    dst: Node,
    self_node: Node,
    duration: int,
    orchestrated: bool,
) -> MeshPathResult:
    _ensure_server(ctx, dst=dst, self_node=self_node, orchestrated=orchestrated)
    if src.id == self_node.id:
        assert ctx.bench is not None
        br = ctx.bench.run(str(dst.ip), duration=duration, bind_ip=str(self_node.ip))
        if not br.success and orchestrated:
            rev = _reverse_from_peer(ctx, src=src, dst=dst, self_node=self_node, duration=duration)
            if rev is not None:
                return rev
        return _from_bench(src, dst, br)
    hop = run_on_peer(
        ctx,
        self_ip=str(self_node.ip),
        node=src,
        remote=(
            remote_iperf_client_cmd(
                dst_ip=str(dst.ip),
                src_ip=str(src.ip),
                duration=duration,
            ),
        ),
        timeout=30.0 + duration,
        connect_timeout=4,
    )
    return _from_remote_client(src, dst, hop.stdout if hop.ok else "", hop)


def _ensure_server(
    ctx: AppContext,
    *,
    dst: Node,
    self_node: Node,
    orchestrated: bool,
) -> None:
    bind = str(require_cluster_ip(str(dst.ip)))
    if dst.id == self_node.id:
        try:
            abs_iperf = ctx.runner.resolve("iperf3")
            ctx.runner.run([abs_iperf, "-s", "-1", "-D", "-B", bind], timeout=5.0)
        except Exception:
            return
        return
    if not orchestrated:
        return
    run_on_peer(
        ctx,
        self_ip=str(self_node.ip),
        node=dst,
        remote=(remote_iperf_server_cmd(bind),),
        timeout=8.0,
        connect_timeout=3,
    )


def _reverse_from_peer(
    ctx: AppContext,
    *,
    src: Node,
    dst: Node,
    self_node: Node,
    duration: int,
) -> MeshPathResult | None:
    """Peer runs the client toward Self (firewall fallback)."""
    hop = run_on_peer(
        ctx,
        self_ip=str(self_node.ip),
        node=dst,
        remote=(
            remote_iperf_client_cmd(
                dst_ip=str(self_node.ip),
                src_ip=str(dst.ip),
                duration=duration,
            ),
        ),
        timeout=30.0 + duration,
        connect_timeout=4,
    )
    if not hop.ok:
        return None
    parsed = _from_remote_client(src, dst, hop.stdout, hop)
    if not parsed.ok:
        return None
    return MeshPathResult(
        src_id=src.id,
        dst_id=dst.id,
        src_ip=str(src.ip),
        dst_ip=str(dst.ip),
        mbps=parsed.mbps,
        retransmits=parsed.retransmits,
        quality=parsed.quality,
        flags=parsed.flags,
        ok=True,
        message="ok (reverse: peer→self)",
        reverse=True,
    )


def _from_bench(src: Node, dst: Node, br: BenchResult) -> MeshPathResult:
    flags = _with_floor(br.mbps, br.flags)
    return MeshPathResult(
        src_id=src.id,
        dst_id=dst.id,
        src_ip=str(src.ip),
        dst_ip=str(dst.ip),
        mbps=br.mbps,
        retransmits=br.retransmits,
        quality=br.quality,
        flags=flags,
        ok=br.success,
        message=br.message,
        reverse=False,
    )


def _from_remote_client(src: Node, dst: Node, stdout: str, hop) -> MeshPathResult:
    mbps, retrans = _parse_iperf_json(stdout) if stdout else (None, None)
    quality, flags = assess_bench_quality(mbps, retransmits=retrans)
    flags = _with_floor(mbps, flags)
    ok = hop.ok and mbps is not None
    return MeshPathResult(
        src_id=src.id,
        dst_id=dst.id,
        src_ip=str(src.ip),
        dst_ip=str(dst.ip),
        mbps=mbps,
        retransmits=retrans,
        quality=quality,
        flags=flags,
        ok=ok,
        message="ok" if ok else (hop.message or hop.stderr or "iperf3 failed")[:200],
        reverse=False,
    )


def _with_floor(mbps: float | None, flags: tuple[str, ...]) -> tuple[str, ...]:
    extra = list(flags)
    if mbps is not None and mbps < TB_TCP_FLOOR_MBPS and "below_tb_tcp_floor" not in extra:
        extra.append("below_tb_tcp_floor")
    return tuple(extra)
