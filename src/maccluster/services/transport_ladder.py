"""Transport ladder for sync: ``rdma`` (arep) → ``tb`` (ssh over bridge0) → ``wifi``.

Pure decision logic. A rung is *available* when:

- ``rdma``: ``arep status --json`` lists this node's peer as ``trusted`` with
  ``"rdma"`` in ``transportCapable`` (arep found a link device to it);
- ``tb``: the node's cluster IP answers on the Thunderbolt bridge;
- ``wifi``: a ``user@host.local`` SSH target can be derived from cluster.toml.

The RDMA data path itself lives in arep; see ``sync_rdma.py`` for the bridge.
"""

from __future__ import annotations

import getpass
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from maccluster.adapters.process import ProcessRunner
from maccluster.app_factory import AppContext
from maccluster.constants import (
    ALLOWLIST_BASENAMES,
    EXTRA_SEARCH_PATHS,
    SEARCH_PATHS,
    TIMEOUT_GENERIC,
    TIMEOUT_PING,
)
from maccluster.domain.enums import ReachabilityState
from maccluster.domain.models import DEFAULT_TRANSPORT_PRIORITY, TRANSPORT_NAMES, Node
from maccluster.errors import CliError
from maccluster.ports.process import ProcessRunnerPort
from maccluster.services.sync_wifi import wifi_ssh_target

__all__ = [
    "AREP_BIN",
    "DEFAULT_TRANSPORT_PRIORITY",
    "TRANSPORT_NAMES",
    "TransportFailed",
    "TransportProbe",
    "arep_peer_for_node",
    "arep_process_runner",
    "arep_status_json",
    "choose_transports",
    "probe_transports",
]

AREP_BIN = "arep"
AREP_TRUST_OK = "trusted"


class TransportFailed(CliError):
    """One rung of the ladder failed; the caller may downgrade to the next."""

    def __init__(self, transport: str, reason: str) -> None:
        super().__init__(f"transport {transport} failed: {reason}", exit_code=1)
        self.transport = transport
        self.reason = reason


@dataclass(frozen=True)
class TransportProbe:
    """Which rungs are usable for one peer right now (plus why-not detail)."""

    rdma_available: bool
    tb_reachable: bool
    wifi_target: str | None
    detail: dict[str, Any] = field(default_factory=dict)

    def is_available(self, name: str) -> bool:
        if name == "rdma":
            return self.rdma_available
        if name == "tb":
            return self.tb_reachable
        if name == "wifi":
            return self.wifi_target is not None
        return False

    def available(self) -> tuple[str, ...]:
        return tuple(n for n in DEFAULT_TRANSPORT_PRIORITY if self.is_available(n))

    def reason(self, name: str) -> str:
        return str(self.detail.get(f"{name}_reason") or "unavailable")


# --- arep status --------------------------------------------------------------------


def _norm_host(value: str) -> str:
    host = str(value).strip().lower()
    return host[: -len(".local")] if host.endswith(".local") else host


def arep_peer_for_node(status: dict | None, node: Node) -> dict | None:
    """Find *node* in the ``peers`` list of ``arep status --json``.

    Matches ``displayName`` (Bonjour name, ``.local`` and case ignored) or
    ``fingerprint`` against the node's hostnames and id. None if absent.
    """
    if not isinstance(status, dict):
        return None
    peers = status.get("peers")
    if not isinstance(peers, list):
        return None
    wanted = {_norm_host(h) for h in node.hostnames} | {_norm_host(node.id)}
    wanted.discard("")
    for peer in peers:
        if not isinstance(peer, dict):
            continue
        name = _norm_host(str(peer.get("displayName") or ""))
        fingerprint = str(peer.get("fingerprint") or "").strip().lower()
        if (name and name in wanted) or (fingerprint and fingerprint in wanted):
            return peer
    return None


def _rdma_capable(peer: dict) -> tuple[bool, str]:
    trust = str(peer.get("trust") or "unknown")
    caps_raw = peer.get("transportCapable")
    caps = [str(c).lower() for c in caps_raw] if isinstance(caps_raw, list) else []
    if trust != AREP_TRUST_OK:
        return False, f"arep peer trust={trust} (run arep pair)"
    if "rdma" not in caps:
        return False, f"arep peer transportCapable={caps} lacks rdma (no link device to peer)"
    return True, ""


def arep_process_runner() -> ProcessRunner:
    """Project ProcessRunner that additionally allows the ``arep`` binary.

    ``arep`` installs to ``~/.local/bin`` (an EXTRA path), so the extra paths
    are searched for every basename here — the allowlist stays tight.
    """
    return ProcessRunner(
        search_paths=(*SEARCH_PATHS, *EXTRA_SEARCH_PATHS),
        allowlist=ALLOWLIST_BASENAMES | frozenset({AREP_BIN}),
    )


def arep_status_json(
    arep_bin: str = AREP_BIN,
    *,
    runner: ProcessRunnerPort | None = None,
    timeout: float = TIMEOUT_GENERIC,
) -> dict | None:
    """``arep status --json`` as a dict; None on any failure (missing, rc≠0, bad JSON)."""
    run = runner or arep_process_runner()
    try:
        res = run.run([arep_bin, "status", "--json"], timeout=timeout)
    except Exception:
        return None
    if res.timed_out or res.returncode != 0:
        return None
    try:
        data = json.loads(res.stdout)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


# --- probing -----------------------------------------------------------------------------


def probe_transports(
    node: Node,
    ctx: AppContext,
    *,
    arep_status: Callable[[], dict | None] | None = None,
    tb_ping: Callable[[str], bool] | None = None,
    wifi_target: Callable[[Node], str | None] | None = None,
) -> TransportProbe:
    """Assess every rung for *node*; probes never raise, failures become detail."""
    detail: dict[str, Any] = {"tb_ip": str(node.ip)}

    rdma = False
    try:
        status = (arep_status or arep_status_json)()
    except Exception as exc:
        status = None
        detail["rdma_reason"] = f"arep status failed: {exc}"
    peer = arep_peer_for_node(status, node)
    if peer is None:
        detail.setdefault("rdma_reason", "arep status has no entry for this peer")
    else:
        detail["arep_peer"] = str(peer.get("displayName") or "")
        detail["arep_trust"] = str(peer.get("trust") or "")
        detail["arep_transport_capable"] = list(peer.get("transportCapable") or [])
        rdma, why = _rdma_capable(peer)
        if why:
            detail["rdma_reason"] = why

    tb = False
    try:
        ping = tb_ping or (
            lambda ip: ctx.reachability.ping(ip, timeout=TIMEOUT_PING).state == ReachabilityState.UP
        )
        tb = bool(ping(str(node.ip)))
        if not tb:
            detail["tb_reason"] = f"{node.ip} not reachable on bridge"
    except Exception as exc:
        detail["tb_reason"] = f"ping failed: {exc}"

    wifi: str | None = None
    try:
        resolve = wifi_target or (lambda n: wifi_ssh_target(n, default_user=getpass.getuser()))
        wifi = resolve(node) or None
        if wifi is None:
            detail["wifi_reason"] = "no *.local hostname / ssh user for peer"
    except Exception as exc:
        detail["wifi_reason"] = f"wifi target failed: {exc}"

    return TransportProbe(rdma_available=rdma, tb_reachable=tb, wifi_target=wifi, detail=detail)


def choose_transports(
    probe: TransportProbe,
    priority: tuple[str, ...] | list[str] = DEFAULT_TRANSPORT_PRIORITY,
    override: str | None = None,
) -> list[str]:
    """Rungs to try, in order: *priority* filtered to available ones.

    *override* forces exactly that rung; it raises ``TransportFailed`` when
    the rung is unavailable and ``ValueError`` for an unknown name.
    """
    names = tuple(priority)
    unknown = [n for n in (*names, *([override] if override else ())) if n not in TRANSPORT_NAMES]
    if unknown:
        raise ValueError(
            f"unknown transport {unknown!r} (allowed: {', '.join(DEFAULT_TRANSPORT_PRIORITY)})"
        )
    if override:
        if probe.is_available(override):
            return [override]
        raise TransportFailed(override, f"unavailable: {probe.reason(override)}")
    return [n for n in names if probe.is_available(n)]
