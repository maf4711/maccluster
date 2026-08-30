"""Cable grade + iperf3 speedtest over TB bridge (startup / explicit CLI)."""

from __future__ import annotations

from pathlib import Path

from maccluster.app_factory import AppContext
from maccluster.cluster_ssh import node_ssh_user, ssh_bind_argv
from maccluster.domain.cable import (
    assess_cluster_cables,
    assess_port,
    iperf_verdict,
)
from maccluster.domain.enums import NodeRole
from maccluster.domain.models import SpeedtestPeerResult, SpeedtestReport
from maccluster.errors import CliError
from maccluster.services.bench_history import record_samples, samples_from_speedtest
from maccluster.services.config_service import load_and_bind_self
from maccluster.services.tb_service import probe_tb
from maccluster.topology.match import best_link_speed, ports_by_peer


def run_speedtest(
    ctx: AppContext,
    *,
    peer: str | None = None,
    duration: int = 5,
    skip_iperf: bool = False,
    try_start_server: bool = True,
    force: bool = False,
    busy_path=None,
    history_path: Path | None = None,
) -> SpeedtestReport:
    """
    1) Classify TB cable/link (40G ideal, 20G ok).
    2) iperf3 client → each peer, bound to Self TB IP.
    3) Append each successful iperf3 sample to the bench history (tb rung).
    """
    cfg, self_node = load_and_bind_self(ctx)
    bind_ip = str(self_node.ip)
    duration = max(1, min(int(duration), 30))
    if not skip_iperf and not force:
        from maccluster.errors import DegradedError
        from maccluster.services.busy_guard import read_busy_state

        busy = read_busy_state(busy_path=busy_path)
        if busy.busy:
            raise DegradedError(f"fabric busy: {busy.reason} — skip saturation")

    try:
        tb = probe_tb(ctx)
    except Exception:
        tb = None
    cable = assess_cluster_cables(tb)

    all_peers = tuple(n for n in cfg.nodes if n.id != self_node.id and n.role != NodeRole.SELF)
    peers = []
    for n in all_peers:
        if peer and peer not in (n.id, str(n.ip)):
            continue
        peers.append(n)
    if peer and not peers:
        raise CliError(f"no peer matched {peer!r}", exit_code=2)

    # Machine-level best Mac↔Mac rate (report header only, never a peer row).
    best_gbps = cable.best_mac_peer_gbps
    # Attribute ports across ALL configured peers so a --peer filter can't
    # turn the single-peer fallback into a wrong attribution.
    peer_tb_ports = ports_by_peer(tb=tb, peers=all_peers)

    results: list[SpeedtestPeerResult] = []
    for n in peers:
        peer_ip = str(n.ip)
        # Each peer row shows ITS link's negotiated rate; the per-link cable
        # classification (assess_port) grades exactly that port.
        matched_ports = peer_tb_ports.get(n.id, ())
        link_gbps = best_link_speed(matched_ports)
        if matched_ports:
            best_port = max(matched_ports, key=lambda p: p.link_speed_gbps or 0.0)
            assessment = assess_port(best_port)
            grade = assessment.grade
            cable_summary = assessment.summary
            cable_good = assessment.good_enough_for_cluster
        else:
            # Port not attributable to this peer — cluster-level view, no rate.
            grade = cable.overall_grade
            cable_summary = cable.summary
            cable_good = cable.good_enough
        iperf_mbps = None
        iperf_ok = False
        iperf_msg = "skipped"
        if not skip_iperf:
            if ctx.bench is None or not ctx.bench.available():
                iperf_msg = "iperf3 not installed locally (brew install iperf3)"
            else:
                if try_start_server:
                    _try_start_remote_iperf(
                        ctx,
                        bind_ip=bind_ip,
                        peer_ip=peer_ip,
                        user=node_ssh_user(n),
                    )
                try:
                    br = ctx.bench.run(peer_ip, duration=duration, bind_ip=bind_ip)
                    if not br.success:
                        # Peer firewall may block inbound data connections;
                        # retry with the peer as client (all outbound).
                        rev = _reverse_iperf(
                            ctx,
                            bind_ip=bind_ip,
                            peer_ip=peer_ip,
                            user=node_ssh_user(n),
                            duration=duration,
                        )
                        if rev is not None:
                            br = rev
                    iperf_ok = br.success
                    iperf_mbps = br.mbps
                    iperf_msg = (
                        iperf_verdict(br.mbps, link_gbps=link_gbps)
                        if br.success
                        else (br.message or "iperf3 failed")
                    )
                except Exception as exc:
                    iperf_msg = str(exc)[:200]
        good = cable_good and (
            skip_iperf
            or (iperf_ok and iperf_mbps is not None and iperf_mbps >= 1000)
            or (not iperf_ok and cable_good)  # cable ok even if iperf needs server
        )
        # For "good enough" on cable-only: use cable; if iperf ran successfully use both
        if not skip_iperf and iperf_ok and iperf_mbps is not None:
            good = cable_good and iperf_mbps >= 1000
        elif not skip_iperf and not iperf_ok:
            good = cable_good  # still good_enough if cable is 40G; note iperf failed

        results.append(
            SpeedtestPeerResult(
                peer_id=n.id,
                peer_ip=peer_ip,
                link_speed_gbps=link_gbps,
                cable_grade=grade.value,
                cable_summary=cable_summary,
                iperf_mbps=iperf_mbps,
                iperf_ok=iperf_ok,
                iperf_message=iperf_msg,
                good_enough=good,
            )
        )

    if not results:
        # Cable-only report when no peers configured
        results.append(
            SpeedtestPeerResult(
                peer_id="(no peer)",
                peer_ip="-",
                link_speed_gbps=best_gbps,
                cable_grade=cable.overall_grade.value,
                cable_summary=cable.summary,
                iperf_mbps=None,
                iperf_ok=False,
                iperf_message="no peers in config",
                good_enough=cable.good_enough,
            )
        )

    report = SpeedtestReport(
        cable_summary=cable.summary,
        cable_grade=cable.overall_grade.value,
        cable_recommendation=cable.recommendation,
        best_link_gbps=best_gbps,
        good_enough=cable.good_enough,
        peers=tuple(results),
        bind_ip=bind_ip,
        duration_s=duration,
    )
    record_samples(samples_from_speedtest(report), path=history_path)
    return report


def reverse_iperf_remote_cmd(bind_ip: str, duration: int) -> str:
    """Remote shell command: peer runs the iperf3 client toward Self."""
    return (
        'export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"; '
        "command -v iperf3 >/dev/null || exit 66; "
        f"iperf3 -c {bind_ip} -t {int(duration)} -J --connect-timeout 5000"
    )


def _reverse_iperf(
    ctx: AppContext,
    *,
    bind_ip: str,
    peer_ip: str,
    user: str | None,
    duration: int,
):
    """Peer-initiated bench (peer client → local server). Works when the
    peer's application firewall blocks inbound iperf3 data connections."""
    from maccluster.adapters.iperf3 import _parse_iperf_json
    from maccluster.domain.models import BenchResult

    try:
        abs_iperf = ctx.runner.resolve("iperf3")
        abs_ssh = ctx.runner.resolve("ssh")
    except Exception:
        return None
    # Local one-off server; ignore failure (port already served)
    try:
        ctx.runner.run([abs_iperf, "-s", "-D", "-1", "-B", bind_ip], timeout=5.0)
    except Exception:
        pass
    try:
        res = ctx.runner.run(
            ssh_bind_argv(
                abs_ssh,
                bind_ip=bind_ip,
                peer_ip=peer_ip,
                user=user,
                connect_timeout=4,
                remote=(reverse_iperf_remote_cmd(bind_ip, duration),),
            ),
            timeout=30.0 + duration,
        )
    except Exception:
        return None
    if res.returncode != 0:
        return None
    parsed = _parse_iperf_json(res.stdout)
    if isinstance(parsed, tuple):
        mbps, retrans = parsed
    else:
        mbps, retrans = parsed, None
    if mbps is None:
        return None
    return BenchResult(
        target=peer_ip,
        mbps=mbps,
        success=True,
        message="ok (reverse: peer→self)",
        retransmits=retrans,
    )


def _try_start_remote_iperf(
    ctx: AppContext, *, bind_ip: str, peer_ip: str, user: str | None = None
) -> None:
    """Best-effort: start `iperf3 -s -D` on peer via TB-bound SSH."""
    try:
        abs_ssh = ctx.runner.resolve("ssh")
    except Exception:
        return
    # daemonize server; ignore failure (already running / no key / no iperf)
    remote = (
        'export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"; '
        "command -v iperf3 >/dev/null || exit 0; "
        "pgrep -x iperf3 >/dev/null || iperf3 -s -D"
    )
    try:
        # One remote argv only (OpenSSH joins multiple with spaces → breaks bash -lc)
        ctx.runner.run(
            ssh_bind_argv(
                abs_ssh,
                bind_ip=bind_ip,
                peer_ip=peer_ip,
                user=user,
                connect_timeout=4,
                remote=(remote,),
            ),
            timeout=8.0,
        )
    except Exception:
        return


def format_speedtest_report(report: SpeedtestReport) -> str:
    lines = [
        "=== TB cable / speedtest (bridge only) ===",
        f"cable: [{report.cable_grade}] {report.cable_summary}",
        f"advice: {report.cable_recommendation}",
    ]
    if report.best_link_gbps is not None:
        lines.append(f"best Mac↔Mac link: {report.best_link_gbps:g} Gb/s")
    if report.bind_ip:
        lines.append(f"iperf bind: {report.bind_ip}  duration: {report.duration_s}s")
    for p in report.peers:
        ge = "YES" if p.good_enough else "CHECK"
        link = f"{p.link_speed_gbps:g}G" if p.link_speed_gbps is not None else "?"
        thr = f"{p.iperf_mbps:.0f} Mbit/s" if p.iperf_mbps is not None else "n/a"
        lines.append(
            f"  peer {p.peer_id} ({p.peer_ip}): link={link}  iperf={thr}  good_enough={ge}"
        )
        lines.append(f"    cable: {p.cable_summary}")
        lines.append(f"    iperf: {p.iperf_message}")
    lines.append(f"overall good_enough_for_cluster: {'YES' if report.good_enough else 'NO'}")
    return "\n".join(lines)
