"""SSH hops bound to the Thunderbolt Self-IP (never Wi-Fi)."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from maccluster.app_factory import AppContext
from maccluster.cluster_ssh import node_ssh_user, require_cluster_ip, ssh_bind_argv
from maccluster.domain.enums import NodeRole
from maccluster.domain.models import ClusterConfig, Node
from maccluster.errors import CliError


@dataclass(frozen=True)
class FleetHopResult:
    node_id: str
    peer_ip: str
    ok: bool
    exit_code: int
    stdout: str
    stderr: str
    skipped: bool = False
    message: str = ""


def iter_peers(
    cfg: ClusterConfig | object,
    self_node: Node,
    *,
    peer: str | None = None,
) -> tuple[Node, ...]:
    """Config-order peers; never self. Optional id or IP filter."""
    out: list[Node] = []
    nodes: Iterable[Node] = getattr(cfg, "nodes", ())
    for n in nodes:
        if n.id == self_node.id or n.role == NodeRole.SELF:
            continue
        if peer and peer not in (n.id, str(n.ip)):
            continue
        out.append(n)
    if peer and not out:
        raise CliError(f"no peer matched {peer!r}", exit_code=2)
    return tuple(out)


def directed_pairs(
    self_node: Node,
    peers: tuple[Node, ...] | list[Node],
    *,
    orchestrated: bool,
) -> tuple[tuple[Node, Node], ...]:
    """Directed (src, dst) pairs. Local-only keeps src == self."""
    members = (self_node, *peers)
    pairs: list[tuple[Node, Node]] = []
    for src in members:
        for dst in members:
            if src.id == dst.id:
                continue
            if not orchestrated and src.id != self_node.id:
                continue
            pairs.append((src, dst))
    return tuple(pairs)


def run_on_peer(
    ctx: AppContext,
    *,
    self_ip: str,
    node: Node,
    remote: tuple[str, ...],
    timeout: float,
    connect_timeout: int = 8,
    user: str | None = None,
    subnet: str | None = None,
) -> FleetHopResult:
    """One TB-bound SSH command on ``node``. Raises on non-cluster IPs."""
    bind = str(require_cluster_ip(self_ip, subnet))
    peer_ip = str(require_cluster_ip(str(node.ip), subnet))
    try:
        abs_ssh = ctx.runner.resolve("ssh")
    except CliError as exc:
        return FleetHopResult(
            node_id=node.id,
            peer_ip=peer_ip,
            ok=False,
            exit_code=exc.exit_code,
            stdout="",
            stderr=exc.message,
            skipped=True,
            message="ssh not found",
        )
    argv = ssh_bind_argv(
        abs_ssh,
        bind_ip=bind,
        peer_ip=peer_ip,
        user=user or node_ssh_user(node),
        connect_timeout=connect_timeout,
        remote=remote,
    )
    result = ctx.runner.run(argv, timeout=timeout)
    return FleetHopResult(
        node_id=node.id,
        peer_ip=peer_ip,
        ok=result.returncode == 0,
        exit_code=result.returncode,
        stdout=result.stdout or "",
        stderr=result.stderr or "",
        skipped=False,
        message="" if result.returncode == 0 else (result.stderr or result.stdout)[:200],
    )
