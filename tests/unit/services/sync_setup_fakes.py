"""Fakes for tests/unit/services/test_sync_setup.py: a recording runner simulating three Macs."""

from __future__ import annotations

import base64
import hashlib
import json
import re
import shlex
from collections.abc import Sequence
from pathlib import Path

from maccluster.errors import CliError
from maccluster.ports.process import ProcessResult

THREE_NODES = """
schema_version = 1
name = "studio-cluster"
subnet = "10.42.0.0/24"
bridge_interface = "bridge0"
heal_interval_seconds = 30
ssh_probes_enabled = false

[[nodes]]
id = "node-a"
hostnames = ["mac-mini-a.local", "mac-mini-a"]
ip = "10.42.0.1"
hw_uuid = "00000000-0000-0000-0000-000000000001"

[[nodes]]
id = "node-b"
hostnames = ["mac-mini-b.local", "mac-mini-b"]
ip = "10.42.0.2"
hw_uuid = "00000000-0000-0000-0000-000000000002"

[[nodes]]
id = "node-c"
hostnames = ["mac-mini-c.local", "mac-mini-c"]
ip = "10.42.0.3"
hw_uuid = "00000000-0000-0000-0000-000000000003"
"""

IPS = {"10.42.0.1": "node-a", "10.42.0.2": "node-b", "10.42.0.3": "node-c"}
LOCAL = {"mac-mini-a.local": "node-a", "mac-mini-b.local": "node-b", "mac-mini-c.local": "node-c"}
LEGACY_CONFIG = (
    'path = "~/Documents"\n'
    'conflict_policy = "newest"\n'
    "skip_on_battery = false\n"
    'excludes = [".DS_Store", "*.custom"]\n'
)
LLM_BLOCK = "".join(
    f'\n[[roots]]  # added by `docsync roots add-llm`\nname = "{n}"\npath = "~/.{n[4:]}"\n'
    f'includes = ["x"]\n'
    for n in ("llm-claude", "llm-codex", "llm-gemini")
)
OWN_PY = "/Users/x/.local/pipx/venvs/maccluster/bin/python"
OWN_ARGS = [
    OWN_PY,
    "-m",
    "docsync",
    "sync",
    "--all",
    "--quiet",
    "--from-agent",
    "--max-runtime",
    "1800",
]
FOREIGN_PY = "/Users/x/.local/pipx/venvs/docsync-lan/bin/python"
FOREIGN_ARGS = [FOREIGN_PY, "-m", "docsync", "sync", "--all", "--max-runtime", "1800"]


def agent_print(args: list[str]) -> str:
    lines = "".join(f"\t\t{a}\n" for a in args)
    return (
        "gui/501/com.a321.docsync = {\n\tactive count = 0\n\tstate = not running\n"
        f"\tprogram = {args[0]}\n\targuments = {{\n{lines}\t}}\n\tlast exit code = 0\n}}\n"
    )


def _sha(text: str | None) -> str:
    return hashlib.sha256((text or "").encode()).hexdigest()


def _script_of(argv: tuple[str, ...]) -> str:
    """The bash script a node runs: `bash -c S` locally, `ssh … '/bin/bash -c S'` remotely."""
    if Path(argv[0]).name == "bash":
        return argv[2]
    return shlex.split(argv[-1])[2]


class FakeNode:
    def __init__(self, name: str) -> None:
        self.name = name
        self.reachable = True
        self.docsync = "1.3.0"
        self.probes_ok = True
        self.config: str | None = LEGACY_CONFIG
        self.backups: dict[str, str] = {}
        self.agent_args: list[str] | None = None  # the plist's ProgramArguments
        self.agent_loaded = False
        self.agent_version = "1.2.0"  # docsync version of a foreign agent program
        self.bootout_fails = False
        self.install_fails = False
        self.aside: dict[
            str, list[str] | None
        ] = {}  # plists moved to LaunchAgents.disabled/…foreign-<stamp>
        self.version_timeouts: list[float] = []
        self.others: list[tuple[str, str]] = []  # other docsync installs: (path, version)
        self.sync_home = True
        self.dotfiles = "behind"  # missing | behind | clean | dirty | diverged
        self.lock_busy = 0  # number of lock probes that report busy
        self.writes = 0


class FakeCluster:
    """Recording fake runner; `# op:<name>` in each script names the node operation."""

    def __init__(self) -> None:
        self.nodes = {n: FakeNode(n) for n in ("node-a", "node-b", "node-c")}
        self.calls: list[tuple[str, ...]] = []
        self.ops: list[tuple[str, str]] = []

    def resolve(self, basename: str) -> str:
        if basename in ("ssh", "bash", "launchctl", "git"):
            return f"/usr/bin/{basename}"
        raise CliError(f"tool not found: {basename}", exit_code=1)

    def _target(self, argv: tuple[str, ...]) -> str:
        if Path(argv[0]).name == "bash":
            return "node-a"  # the local Mac (fake_ctx binds self = node-a)
        for a in argv:
            host = a.split("@", 1)[1] if "@" in a else ""
            if host in IPS:
                return IPS[host]
            if host in LOCAL:
                return LOCAL[host]
        raise AssertionError(f"no node in {argv}")

    def run(self, argv: Sequence[str], *, timeout: float = 15.0, check: bool = False):
        full = tuple(argv)
        self.calls.append(full)
        text = _script_of(full)
        m = re.search(r"# op:([a-z-]+)", text)
        assert m, f"unexpected command {full}"
        op, name = m.group(1), self._target(full)
        self.ops.append((name, op))
        node = self.nodes[name]
        if not node.reachable and Path(full[0]).name == "ssh":
            return ProcessResult(full, 255, "", "ssh: connect timed out")
        out, rc = self._op(node, op, text, timeout)
        return ProcessResult(full, rc, out, "" if rc == 0 else out)

    def _op(self, node: FakeNode, op: str, text: str, timeout: float) -> tuple[str, int]:
        if op == "probe":
            return "", 0
        if op == "docsync":
            ok = "add-llm ok\nicloud-check ok\n" if node.probes_ok else ""
            return f"docsync {node.docsync}\n{ok}", 0
        if op == "lock":
            if node.lock_busy:
                node.lock_busy -= 1
                return "busy: another run is in progress (pid 7, running 3 min, docsync sync)", 0
            return "free", 0
        if op == "read-config":
            return (node.config, 0) if node.config is not None else ("", 1)
        if op == "write-config":
            node.writes += 1
            stamp = re.search(r"\.bak-(\d{8}T\d{6}Z)", text).group(1)
            if node.config is not None:
                node.backups[stamp] = node.config
            node.config = base64.b64decode(
                re.search(r"printf %s '([^']+)'", text).group(1)
            ).decode()
            return "", 0
        if op == "add-llm":
            node.writes += 1
            node.config += LLM_BLOCK
            return "added roots llm-claude, llm-codex, llm-gemini", 0
        if op == "agent-status":
            out = f"plist={int(node.agent_args is not None)}\n"
            if node.agent_args is not None:
                out += f"plistargs={json.dumps(node.agent_args)}\n"
            out += f"py={OWN_PY}\n"
            return out + (agent_print(node.agent_args) if node.agent_loaded else ""), 0
        if op == "agent-bootout":
            node.agent_loaded = node.bootout_fails
            return "", 0
        if op == "agent-plist-aside":
            node.writes += 1
            stamp = re.search(r"\.foreign-(\d{8}T\d{6}Z)", text).group(1)
            node.aside[stamp] = node.agent_args
            node.agent_args = None
            return "", 0
        if op == "agent-version":
            node.version_timeouts.append(timeout)
            return f"docsync {node.agent_version}", 0
        if op == "docsync-others":
            return "".join(f"other={p}|docsync {v}\n" for p, v in node.others), 0
        if op == "install-agent":
            if node.install_fails:
                return "RuntimeError: launchctl bootstrap failed: 5: Input/output error", 1
            node.writes += 1
            node.agent_args, node.agent_loaded = list(OWN_ARGS), True
            return "installed", 0
        if op == "sync-home-status":
            return f"loaded={int(node.sync_home)} plist={int(node.sync_home)}", 0
        if op == "sync-home-remove":
            node.writes += 1
            node.sync_home = False
            return "", 0
        if op == "dotfiles-status":
            state = {"missing": "missing", "dirty": "dirty"}.get(node.dotfiles, "clean")
            return state, 0
        if op == "dotfiles-clone":
            node.writes += 1
            node.dotfiles = "clean"
            return "Cloning into '/Users/x/dotfiles'...", 0
        if op == "dotfiles-fetch":
            counts = {"behind": "0\t18", "clean": "0\t0", "diverged": "1\t18"}[node.dotfiles]
            return counts, 0
        if op == "dotfiles-ff":
            node.writes += 1
            node.dotfiles = "clean"
            return "", 0
        raise AssertionError(f"unknown op {op}")

    def node_ops(self, name: str) -> list[str]:
        return [op for n, op in self.ops if n == name]

    def snapshot(self) -> dict[str, tuple[str, str]]:
        return {n: (_sha(x.config), _sha(repr(x.agent_args))) for n, x in self.nodes.items()}
