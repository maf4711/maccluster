"""Shared ensure path for up / heal one-shot."""

from __future__ import annotations

from maccluster.app_factory import AppContext
from maccluster.config.paths import default_lock_path
from maccluster.domain.enums import HealActionKind, LinkState
from maccluster.domain.models import MutateResult
from maccluster.errors import CliError, DegradedError, PrivilegeError
from maccluster.heal_logic.idempotency import is_noop, summarize
from maccluster.heal_logic.plan import plan_ensure
from maccluster.mapping.receptacle import resolve_target_interface
from maccluster.platform.guard import assert_supported_for_mutate
from maccluster.services.config_service import load_and_bind_self
from maccluster.services.tb_service import probe_tb


def ensure_local(ctx: AppContext, *, dry_run: bool = False) -> MutateResult:
    """Ensure bridge + Self IP on local host. Raises CliError / DegradedError."""
    info = ctx.platform.get_platform()
    assert_supported_for_mutate(info)

    cfg, self_node = load_and_bind_self(ctx)

    lock_path = default_lock_path()
    with ctx.lock.acquire(lock_path):
        try:
            tb = probe_tb(ctx)
        except CliError:
            tb = None

        ifaces = None
        try:
            ifaces = ctx.net_read.list_interfaces()
        except Exception:
            ifaces = None

        interface = resolve_target_interface(
            config_bridge=cfg.bridge_interface,
            tb=tb,
            available_ifaces=ifaces,
        )

        observed = ctx.net_read.get_bridge(interface)
        actions = plan_ensure(
            interface=interface,
            desired_ip=self_node.ip,
            observed=observed,
        )

        tb_links = (
            sum(1 for p in tb.ports if p.link_state == LinkState.CONNECTED) if tb else 0
        )
        result = MutateResult(
            actions=list(actions),
            interface=interface,
            ip=str(self_node.ip),
            already_configured=is_noop(actions),
            tb_link_present=tb_links > 0,
            tb_links=tb_links,
            message=summarize(actions),
        )

        if result.already_configured:
            ctx.audit.record("ensure", "noop", iface=interface, ip=str(self_node.ip))
            if not dry_run:
                try:
                    ctx.net_apply.protect_wifi_from_bridge(str(self_node.ip), dry_run=dry_run)
                except Exception:
                    pass
            if not result.tb_link_present:
                raise DegradedError(
                    f"already configured {interface} {self_node.ip}; no TB link",
                    details=result,
                )
            return result

        # Apply
        try:
            needs_ip = any(
                a.kind in (HealActionKind.SET_IP, HealActionKind.ENSURE_BRIDGE) for a in actions
            )
            needs_up = any(
                a.kind in (HealActionKind.ADMIN_UP, HealActionKind.ENSURE_BRIDGE) for a in actions
            )
            if needs_ip or needs_up or any(a.kind == HealActionKind.ENSURE_BRIDGE for a in actions):
                ctx.net_apply.ensure_bridge_and_ip(
                    interface,
                    self_node.ip,
                    prefixlen=cfg.subnet.prefixlen,
                    dry_run=dry_run,
                )
            elif needs_up:
                ctx.net_apply.admin_up(interface, dry_run=dry_run)
        except PrivilegeError:
            ctx.audit.record("ensure", "privilege_error", iface=interface)
            raise
        except CliError:
            ctx.audit.record("ensure", "error", iface=interface)
            raise

        ctx.audit.record("ensure", "ok", iface=interface, ip=str(self_node.ip))
        if not dry_run:
            try:
                ctx.net_apply.protect_wifi_from_bridge(str(self_node.ip), dry_run=dry_run)
            except Exception:
                pass
        result.message = f"configured {interface} {self_node.ip}"

        if not result.tb_link_present:
            raise DegradedError(
                f"configured {interface} {self_node.ip}; no TB link",
                details=result,
            )
        return result
