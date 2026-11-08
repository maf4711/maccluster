"""Run one small bash script on a cluster node: locally for this Mac, over ssh for a peer.

Peers are reached the way `remote_install` reaches them (ssh bound to this Mac's bridge IP),
with the `.local` names as fallback when the Thunderbolt address does not answer. Every script
starts with ``# op:<name>`` so logs and tests can tell the operations apart.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass

from maccluster.cluster_ssh import CLUSTER_SSH_BASE_OPTS, node_ssh_user, ssh_bind_argv
from maccluster.domain.models import Node
from maccluster.ports.process import ProcessResult
from maccluster.services.sync_docsync import _hosts

PROBE_TIMEOUT_S = 5

# The interpreter next to the `maccluster` command (as `inject_script` finds it), else the two
# venv layouts maccluster is installed with: pipx and the remote-install venv.
PY_RESOLVE = (
    'export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:$PATH"\n'
    'py=""\n'
    "if cli=$(command -v maccluster 2>/dev/null); then\n"
    "  d=$(python3 -c 'import os,sys; print(os.path.dirname(os.path.realpath(sys.argv[1])))' \"$cli\")\n"
    '  [ -x "$d/python" ] && py="$d/python"\n'
    "fi\n"
    'for c in "$HOME/.local/pipx/venvs/maccluster/bin/python" '
    '"$HOME/.local/share/maccluster/venv/bin/python"; do\n'
    '  [ -z "$py" ] && [ -x "$c" ] && py="$c"\n'
    "done\n"
    '[ -n "$py" ] || { echo "no maccluster python on this Mac"; exit 127; }\n'
)


def script(op: str, body: str, *, python: bool = False) -> str:
    return f"# op:{op}\n" + (PY_RESOLVE if python else "") + body


@dataclass
class NodeShell:
    """Where and how to run scripts on one node; `host` is None for this Mac."""

    ctx: object
    node: Node
    self_ip: str
    host: str | None = None
    user: str = ""

    @property
    def local(self) -> bool:
        return self.host is None

    def argv(self, text: str, *, connect_timeout: int = 15) -> list[str]:
        runner = self.ctx.runner
        if self.local:
            return [runner.resolve("bash"), "-c", text]
        remote = shlex.join(["/bin/bash", "-c", text])
        ssh = runner.resolve("ssh")
        if self.host == str(self.node.ip):
            return ssh_bind_argv(
                ssh,
                bind_ip=self.self_ip,
                peer_ip=self.host,
                user=self.user,
                connect_timeout=connect_timeout,
                remote=(remote,),
            )
        return [
            ssh,
            *CLUSTER_SSH_BASE_OPTS,
            "-o",
            f"ConnectTimeout={connect_timeout}",
            f"{self.user}@{self.host}",
            remote,
        ]

    def run(self, text: str, *, timeout: float = 60.0) -> ProcessResult:
        return self.ctx.runner.run(self.argv(text), timeout=timeout)


def connect(ctx, node: Node, *, self_node: Node) -> NodeShell | None:
    """A shell on `node`: this Mac directly, a peer on the first of [TB IP, .local names] that
    answers ssh within PROBE_TIMEOUT_S; None when none does."""
    if node.id == self_node.id:
        return NodeShell(ctx, node, str(self_node.ip))
    user = node_ssh_user(node)
    for host in _hosts(node, "tb"):
        shell = NodeShell(ctx, node, str(self_node.ip), host=host, user=user)
        argv = shell.argv(script("probe", "true"), connect_timeout=PROBE_TIMEOUT_S)
        try:
            res = ctx.runner.run(argv, timeout=PROBE_TIMEOUT_S + 3)
        except Exception:  # an unknown host or a runner error: try the next name
            continue
        if res.returncode == 0:
            return shell
    return None
