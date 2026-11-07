"""Persisted options and lifecycle of the optional automation schedule."""

from __future__ import annotations

import json
import math
from pathlib import Path

from maccluster.app_factory import AppContext
from maccluster.domain.models import ServiceState
from maccluster.errors import CliError

AUTOMATION_LABEL = "com.maccluster.automation"
DEFAULT_INTERVAL = 86400
MIN_INTERVAL = 300


def settings_path(ctx: AppContext) -> Path:
    return ctx.config_path.expanduser().resolve().with_name("automation.json")


def _validate(data: object) -> dict:
    if not isinstance(data, dict):
        raise CliError("automation settings must be an object", exit_code=2)
    required = {"source", "local_only", "peer", "no_update", "timeout", "sync"}
    if not required <= set(data) or set(data) - required - {"expected_sha256"}:
        raise CliError("automation settings have missing or unknown fields", exit_code=2)
    if not isinstance(data["source"], str) or not data["source"].strip():
        raise CliError("automation source must not be empty", exit_code=2)
    from maccluster.services.package_update import validate_expected_sha256, validate_source

    data["source"] = validate_source(data["source"], require_exists=data["no_update"] is not True)
    data["expected_sha256"] = validate_expected_sha256(data["source"], data.get("expected_sha256"))
    if data["no_update"] is True and data["expected_sha256"] is not None:
        raise CliError("--sha256 cannot be combined with --no-update", exit_code=2)
    for key in ("local_only", "no_update", "sync"):
        if type(data[key]) is not bool:
            raise CliError(f"automation {key} must be a boolean", exit_code=2)
    if data["peer"] is not None and (not isinstance(data["peer"], str) or not data["peer"]):
        raise CliError("automation peer must be a nonempty node id", exit_code=2)
    timeout = data["timeout"]
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
        raise CliError("automation timeout must be finite and positive", exit_code=2)
    if data["local_only"] and (data["peer"] or data["sync"]):
        raise CliError("--local-only cannot be combined with --peer or --sync", exit_code=2)
    return data


def load_automation_settings(ctx: AppContext) -> dict:
    path = settings_path(ctx)
    try:
        return _validate(json.loads(ctx.fs.read_text(path)))
    except (OSError, ValueError) as exc:
        raise CliError(f"cannot read automation settings {path}: {exc}", exit_code=2) from exc


def install_automation_service(
    ctx: AppContext,
    *,
    interval_seconds: int | None = None,
    source: str,
    expected_sha256: str | None = None,
    local_only: bool = False,
    peer: str | None = None,
    no_update: bool = False,
    timeout: float = 600.0,
    sync: bool = False,
) -> ServiceState:
    from maccluster.services.config_service import load_and_bind_self
    from maccluster.services.service_mgmt import _resolve_maccluster_program

    interval = DEFAULT_INTERVAL if interval_seconds is None else interval_seconds
    if interval < MIN_INTERVAL:
        raise CliError(f"automation interval must be >= {MIN_INTERVAL} seconds", exit_code=2)
    options = _validate(
        dict(
            source=source,
            expected_sha256=expected_sha256,
            local_only=local_only,
            peer=peer,
            no_update=no_update,
            timeout=timeout,
            sync=sync,
        )
    )
    cfg, self_node = load_and_bind_self(ctx)
    if peer and not any(n.id != self_node.id and peer in (n.id, str(n.ip)) for n in cfg.nodes):
        raise CliError(f"unknown automation peer: {peer}", exit_code=2)
    program = _resolve_maccluster_program()
    path = settings_path(ctx)
    ctx.fs.write_text_atomic(path, json.dumps(options, indent=2, allow_nan=False) + "\n")
    return ctx.service.install(
        program=program,
        config_path=ctx.config_path,
        interval_seconds=interval,
        label=AUTOMATION_LABEL,
    )


def automation_service_status(ctx: AppContext) -> ServiceState:
    return ctx.service.status(label=AUTOMATION_LABEL)


def uninstall_automation_service(ctx: AppContext) -> ServiceState:
    return ctx.service.uninstall(label=AUTOMATION_LABEL)
