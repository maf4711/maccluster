"""Configuration: ~/.config/docsync/config.toml, peers, maccluster import."""

from __future__ import annotations

import os
import socket
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

CONFIG_DIR = Path(os.environ.get("DOCSYNC_CONFIG_DIR", "~/.config/docsync")).expanduser()
CONFIG_FILE = CONFIG_DIR / "config.toml"
STATE_DIR = Path(os.environ.get("DOCSYNC_STATE_DIR", "~/.local/state/docsync")).expanduser()
MACCLUSTER_CONFIG = Path("~/.config/maccluster/cluster.toml").expanduser()

DEFAULT_EXCLUDES = [
    ".DS_Store", ".localized", ".Trash", ".Trashes", ".TemporaryItems",
    ".Spotlight-V100", ".fseventsd", ".DocumentRevisions-V100", ".docsync*",
    "*.icloud", "~$*", ".~lock.*", "*.swp", "*.tmp", "*.part", "Thumbs.db",
    "*.photoslibrary", "*.sparsebundle",
]


@dataclass
class Peer:
    name: str
    hosts: list[str]
    user: str = ""
    path: str = "~/Documents"

    def userhost(self, host: str) -> str:
        return f"{self.user}@{host}" if self.user else host


@dataclass
class Config:
    path: str = "~/Documents"
    excludes: list[str] = field(default_factory=lambda: list(DEFAULT_EXCLUDES))
    peers: list[Peer] = field(default_factory=list)
    conflict_policy: str = "newest"  # newest | local | remote
    trash: bool = True  # move deletions to ~/.Trash instead of unlinking

    @property
    def local_root(self) -> Path:
        return Path(self.path).expanduser()

    def peer(self, name: str) -> Peer:
        for p in self.peers:
            if p.name == name:
                return p
        raise KeyError(f"unknown peer '{name}' (known: {', '.join(p.name for p in self.peers) or '-'})")


def parse_config(text: str) -> Config:
    data = tomllib.loads(text)
    cfg = Config(
        path=data.get("path", "~/Documents"),
        excludes=list(data.get("excludes", DEFAULT_EXCLUDES)),
        conflict_policy=data.get("conflict_policy", "newest"),
        trash=bool(data.get("trash", True)),
    )
    if cfg.conflict_policy not in ("newest", "local", "remote"):
        raise ValueError(f"conflict_policy must be newest|local|remote, got {cfg.conflict_policy!r}")
    for raw in data.get("peers", []):
        if "name" not in raw or not raw.get("hosts"):
            raise ValueError(f"peer needs 'name' and non-empty 'hosts': {raw}")
        cfg.peers.append(Peer(
            name=str(raw["name"]), hosts=[str(h) for h in raw["hosts"]],
            user=str(raw.get("user", "")), path=str(raw.get("path", cfg.path)),
        ))
    return cfg


def load_config(file: Path = CONFIG_FILE) -> Config:
    if not file.exists():
        raise FileNotFoundError(f"no config at {file} — run: docsync init")
    return parse_config(file.read_text())


def _toml_str(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def render_config(cfg: Config) -> str:
    lines = [
        "# docsync configuration",
        f"path = {_toml_str(cfg.path)}",
        f"conflict_policy = {_toml_str(cfg.conflict_policy)}  # newest | local | remote",
        f"trash = {'true' if cfg.trash else 'false'}  # deletions go to ~/.Trash/docsync-*/",
        "excludes = [",
        *[f"  {_toml_str(e)}," for e in cfg.excludes],
        "]",
    ]
    for p in cfg.peers:
        lines += [
            "",
            "[[peers]]",
            f"name = {_toml_str(p.name)}",
            f"hosts = [{', '.join(_toml_str(h) for h in p.hosts)}]  # tried in order",
            f"user = {_toml_str(p.user)}",
            f"path = {_toml_str(p.path)}",
        ]
    return "\n".join(lines) + "\n"


def save_config(cfg: Config, file: Path = CONFIG_FILE) -> None:
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(render_config(cfg))


def local_hostnames() -> set[str]:
    names = {socket.gethostname()}
    short = names.copy()
    for n in list(names):
        short.add(n.split(".")[0])
    out = set()
    for n in short:
        out.add(n.lower())
        # macOS appends "-2", "-6", " (3)" to hostnames on name clashes
        base = n.lower()
        while base and (base[-1].isdigit() or base[-1] in "-() "):
            base = base[:-1]
        if base:
            out.add(base)
    return out


def _is_self(hostnames: list[str], mine: set[str]) -> bool:
    for h in hostnames:
        base = h.lower().replace(".local", "")
        while base and (base[-1].isdigit() or base[-1] in "-() "):
            base = base[:-1]
        if base in mine:
            return True
    return False


def peers_from_maccluster(text: str, mine: set[str], user: str = "") -> list[Peer]:
    """Build peers from a maccluster cluster.toml (nodes with ip + hostnames)."""
    data = tomllib.loads(text)
    peers = []
    for node in data.get("nodes", []):
        hostnames = [str(h) for h in node.get("hostnames", [])]
        if _is_self(hostnames, mine):
            continue
        hosts: list[str] = []
        if node.get("ip"):
            hosts.append(str(node["ip"]))
        # prefer plain .local names, skip the "(N)" variants Bonjour invents
        for h in hostnames:
            if h.endswith(".local") and "(" not in h and h.lower() not in [x.lower() for x in hosts]:
                hosts.append(h)
        if not hosts:
            continue
        peers.append(Peer(name=str(node.get("id", hosts[0])), hosts=hosts,
                          user=str(node.get("user", user))))
    return peers
