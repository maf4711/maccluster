"""CLI entry point."""

from __future__ import annotations

import os
import sys
import traceback
from collections.abc import Sequence

from maccluster.app_factory import AppContext
from maccluster.cli.exit_codes import ERROR, OK, USAGE
from maccluster.cli.parser import build_parser
from maccluster.errors import CliError


def main(argv: Sequence[str] | None = None) -> int:
    argv_list = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    try:
        args = parser.parse_args(argv_list)
    except SystemExit as exc:
        # argparse uses SystemExit for -h and errors
        code = exc.code
        if code is None:
            return OK
        return int(code) if int(code) == 0 else USAGE

    if not getattr(args, "command", None):
        parser.print_help()
        return USAGE

    # config subcommand without action
    if args.command == "config" and not getattr(args, "config_action", None):
        args.config_action = "show"
    if args.command == "service" and not getattr(args, "service_action", None):
        print(
            "error: service requires install|uninstall|status|"
            "sync-install|sync-uninstall|sync-status",
            file=sys.stderr,
        )
        return USAGE
    if args.command == "sync" and not getattr(args, "sync_action", None):
        print("error: sync requires a target (e.g. home)", file=sys.stderr)
        return USAGE
    if args.command == "keychain" and not getattr(args, "keychain_action", None):
        args.keychain_action = "show"

    no_color = bool(os.environ.get("NO_COLOR", "").strip())
    try:
        ctx = AppContext.production(
            config=getattr(args, "config", None),
            json_mode=bool(getattr(args, "json", False)),
            verbose=bool(getattr(args, "verbose", False)),
            no_color=no_color,
        )
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return ERROR

    handler = _dispatch(args.command)
    if handler is None:
        print(f"error: unknown command {args.command!r}", file=sys.stderr)
        return USAGE

    try:
        return int(handler(ctx, args))
    except CliError as exc:
        if not ctx.json_mode:
            print(f"error: {exc.message}", file=sys.stderr)
        else:
            from maccluster.render.json_out import dumps

            print(dumps(args.command, {"error": exc.message, "exit_code": exc.exit_code}))
        if ctx.verbose:
            traceback.print_exc()
        return int(exc.exit_code)
    except KeyboardInterrupt:
        return OK
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        if ctx.verbose:
            traceback.print_exc()
        return ERROR


def _dispatch(command: str):
    from maccluster.commands import (
        bench,
        config_cmd,
        doctor,
        heal,
        init_cmd,
        keychain_cmd,
        monitor,
        remote_install_cmd,
        service_cmd,
        speedtest_cmd,
        ssh_config_cmd,
        status,
        sync_cmd,
        tb,
        topo,
        up,
    )

    table = {
        "tb": tb.run,
        "init": init_cmd.run,
        "config": config_cmd.run,
        "up": up.run,
        "heal": heal.run,
        "status": status.run,
        "monitor": monitor.run,
        "topo": topo.run,
        "doctor": doctor.run,
        "bench": bench.run,
        "speedtest": speedtest_cmd.run,
        "service": service_cmd.run,
        "sync": sync_cmd.run,
        "remote-install": remote_install_cmd.run,
        "ssh-config": ssh_config_cmd.run,
        "keychain": keychain_cmd.run,
    }
    return table.get(command)


if __name__ == "__main__":
    raise SystemExit(main())
