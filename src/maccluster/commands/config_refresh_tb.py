"""config refresh-tb — live Thunderbolt ids of this Mac as a cluster.toml snippet.

Dry-run by default: prints what ``tb_domain_uuids`` / ``tb_controller_uids``
the self node should carry now. Only ``--apply`` rewrites those two arrays in
cluster.toml (text splice, everything else byte-for-byte, ``.bak-<ts>`` kept).
"""

from __future__ import annotations

from datetime import UTC, datetime

from maccluster.adapters.tb_system_profiler import run_system_profiler_json
from maccluster.app_factory import AppContext
from maccluster.cli.exit_codes import OK
from maccluster.config.load import load_toml_text
from maccluster.domain.models import ThunderboltSnapshot
from maccluster.errors import CliError
from maccluster.mapping.refresh_tb import render_refresh_snippet, splice_node_tb_ids
from maccluster.mapping.tb_identity import (
    check_tb_identity,
    live_controller_uids,
    live_domain_uuids,
    parse_system_profiler_json,
)
from maccluster.render.json_out import dumps
from maccluster.services.config_service import load_and_bind_self
from maccluster.services.tb_service import probe_tb


def probe_tb_ids(ctx: AppContext) -> ThunderboltSnapshot:
    """Structured ``system_profiler -json`` first (it carries the controller UIDs);
    the text/ioreg probe only when that yields no ports."""
    try:
        snap = parse_system_profiler_json(run_system_profiler_json(ctx.runner))
        if snap.ports:
            return snap
    except Exception:
        pass
    return probe_tb(ctx)


def run(ctx: AppContext, args) -> int:
    apply = bool(getattr(args, "apply", False))
    cfg, self_node = load_and_bind_self(ctx)
    tb = probe_tb_ids(ctx)
    uuids = live_domain_uuids(tb)
    uids = live_controller_uids(tb)
    if not uuids and not uids:
        raise CliError("no Thunderbolt bus identities found (system_profiler)", exit_code=1)
    snippet = render_refresh_snippet(
        cfg=cfg, self_node=self_node, tb=tb, config_path=str(ctx.config_path), apply=apply
    )
    verdict = check_tb_identity(self_node, tb)

    written = False
    backup: str | None = None
    if apply:
        path = ctx.config_path
        before = path.read_text(encoding="utf-8")
        after = splice_node_tb_ids(before, self_node.id, domain_uuids=uuids, controller_uids=uids)
        load_toml_text(after)  # must still be a valid cluster.toml before anything is written
        if after != before:
            stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
            bak = path.with_name(f"{path.name}.bak-{stamp}")
            bak.write_text(before, encoding="utf-8")
            path.write_text(after, encoding="utf-8")
            written, backup = True, str(bak)

    if ctx.json_mode:
        print(
            dumps(
                "config.refresh-tb",
                {
                    "self": self_node.id,
                    "config_path": str(ctx.config_path),
                    "source": tb.source,
                    "live": {"tb_domain_uuids": list(uuids), "tb_controller_uids": list(uids)},
                    "verdict": {
                        "check_id": verdict.check_id,
                        "severity": verdict.severity.value,
                        "summary": verdict.summary,
                        "detail": verdict.detail,
                    },
                    "snippet": snippet,
                    "apply": apply,
                    "written": written,
                    "backup": backup,
                },
            )
        )
        return OK
    print(snippet, end="")
    if apply:
        if written:
            print(f"written: {ctx.config_path} (backup: {backup})")
        else:
            print(f"unchanged: {ctx.config_path} already carries the live ids")
    return OK
