"""ssh-config — write OpenSSH fragment so cluster peers use TB BindAddress only."""

from __future__ import annotations

from maccluster.app_factory import AppContext
from maccluster.cluster_ssh import write_cluster_ssh_config
from maccluster.render.json_out import dumps
from maccluster.services.config_service import load_and_bind_self


def run(ctx: AppContext, args) -> int:
    cfg, self_node = load_and_bind_self(ctx)
    path = write_cluster_ssh_config(
        self_ip=self_node.ip,
        subnet=cfg.subnet,
        user=getattr(args, "user", None),
    )
    data = {
        "path": str(path),
        "bind_ip": str(self_node.ip),
        "subnet": str(cfg.subnet),
        "message": "cluster SSH will bind to bridge Self-IP only (not Wi‑Fi)",
    }
    if ctx.json_mode:
        print(dumps("ssh-config", data))
    else:
        print(f"wrote {path}")
        print(f"  BindAddress {self_node.ip} for peers in {cfg.subnet}")
        print("  (Wi‑Fi/LAN addresses are refused by maccluster remote-install / sync)")
    return 0
