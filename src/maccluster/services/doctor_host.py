"""Host findings for doctor --host [--fleet]."""

from __future__ import annotations

from maccluster.adapters.host_macos import HostMacOS
from maccluster.app_factory import AppContext
from maccluster.doctor_logic import checks
from maccluster.doctor_logic.host_parse import snapshot_from_json
from maccluster.domain.models import ClusterConfig, DoctorFinding, HostSnapshot, Node
from maccluster.services.fleet_exec import iter_peers, run_on_peer

HOST_FLEET_TIMEOUT_S = 4.0

# Remote prints one JSON object of raw tool dumps; parsed locally (16K pages stay here).
_REMOTE_HOST_PY = (
    "import json,shutil,subprocess;"
    "def R(c):"
    " p=subprocess.run(c,capture_output=True,text=True,timeout=1.5);"
    " return p.stdout or '';"
    "d={'vm_stat':R(['vm_stat']),'df':R(['df','-P','/']),"
    "'uptime':R(['uptime']),'pmset':R(['pmset','-g','therm']),"
    "'pmset_g':R(['pmset','-g'])};"
    "s=shutil.which('sntp');"
    "d['sntp_missing']=s is None;"
    "d['sntp']=(R([s,'-d','time.apple.com']) if s else None);"
    "r=shutil.which('rdma_ctl');"
    "d['rdma_missing']=r is None;"
    "d['rdma']=(R([r,'status']) if r else None);"
    "print(json.dumps(d,separators=(',',':')))"
)
REMOTE_HOST_SNAPSHOT_CMD = "python3 -c " + repr(_REMOTE_HOST_PY)


def findings_from_snapshot(
    snap: HostSnapshot,
    *,
    peer: bool = False,
) -> list[DoctorFinding]:
    if snap.error:
        findings = [checks.check_host(snap, peer=peer)]
        if peer:
            # unreadable node: power stays INFO (host already carries the WARN)
            findings.append(checks.check_power(snap, peer=peer))
        return findings
    findings = [
        checks.check_host(snap, peer=peer),
        checks.check_disk(snap, peer=peer),
        checks.check_thermal(snap, peer=peer),
        checks.check_ntp(snap, peer=peer),
    ]
    if peer:
        # self RDMA already covered by the always-on top-level `rdma` check
        findings.append(checks.check_rdma_host(snap, peer=peer))
        # sleep/powernap regression class: a peer that dozes breaks the cluster
        findings.append(checks.check_power(snap, peer=peer))
    return findings


def collect_local(ctx: AppContext, node_id: str) -> HostSnapshot:
    host = getattr(ctx, "host", None)
    if host is not None:
        return host.snapshot(node_id)
    return HostMacOS(ctx.runner).snapshot(node_id)


def collect_fleet(
    ctx: AppContext,
    cfg: ClusterConfig,
    self_node: Node,
    *,
    peer: str | None = None,
) -> list[HostSnapshot]:
    snaps: list[HostSnapshot] = []
    for node in iter_peers(cfg, self_node, peer=peer):
        hop = run_on_peer(
            ctx,
            self_ip=str(self_node.ip),
            node=node,
            remote=(REMOTE_HOST_SNAPSHOT_CMD,),
            timeout=HOST_FLEET_TIMEOUT_S,
            connect_timeout=3,
        )
        if hop.skipped or not hop.ok:
            snaps.append(
                HostSnapshot(
                    node_id=node.id,
                    ram_used_gb=None,
                    ram_free_gb=None,
                    load_1m=None,
                    disk_free_gb=None,
                    cpu_speed_limit_pct=None,
                    ntp_offset_s=None,
                    error="unreachable",
                )
            )
            continue
        snaps.append(snapshot_from_json(node.id, hop.stdout))
    return snaps
