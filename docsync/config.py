"""Configuration: ~/.config/docsync/config.toml, peers, maccluster import."""

from __future__ import annotations

import os
import re
import socket
import tomllib
from dataclasses import dataclass, field, replace
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
# Never listed or sent in a root with `includes`, even when an include names them (matched against
# every path component, ignoring case). Roots without includes do not use this list.
DENY_PATTERNS = [".credentials.json", "auth.json", "oauth_creds.json", "*.pem", "id_rsa*", "id_ed25519*", ".env"]
# `docsync roots add-llm`: LLM tool configs, allowlist only (sessions, caches and logins stay per Mac).
LLM_ROOTS = [
    ("llm-claude", "~/.claude", ["CLAUDE.md", "settings.json", "MEMORY.md", "RTK.md", "projects/*/memory/**"]),
    ("llm-codex", "~/.codex", ["config.toml", "AGENTS.md", "rules/**"]),
    ("llm-gemini", "~/.gemini", ["settings.json", "GEMINI.md"]),
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
class Root:
    """One synced folder. `peer_path` defaults to `path`; the legacy top-level `path` becomes the
    root "documents" and keeps using each peer's own `path` for backwards compatibility."""
    name: str
    path: str
    peer_path: str = ""
    excludes: list[str] | None = None
    legacy: bool = False
    includes: list[str] | None = None  # allowlist of root-relative globs; None = the whole tree


ROOT_NAME = re.compile(r"^[A-Za-z0-9_-]+$")


@dataclass
class Config:
    path: str = "~/Documents"
    excludes: list[str] = field(default_factory=lambda: list(DEFAULT_EXCLUDES))
    peers: list[Peer] = field(default_factory=list)
    conflict_policy: str = "newest"  # newest | local | remote
    trash: bool = True  # move deletions to ~/.Trash instead of unlinking
    case_insensitive: bool = True  # APFS default: 'Befunde' and 'befunde' are the same folder
    max_delete: int = 25  # deletion guard: more deletions per run need --force-deletes
    skip_on_battery: bool = False  # launchd runs do nothing while on battery power
    transfer_streams: int = 4  # parallel rsync/ssh streams per peer for large transfers
    roots: list[Root] = field(default_factory=list)  # empty → the legacy `path` as root "documents"
    includes: list[str] | None = None  # set by for_root(): the root's allowlist
    safetynet: bool = True  # back up files before they are overwritten
    safetynet_keep: int = 5
    safetynet_max_age_days: float = 14.0
    safetynet_max_gb: float = 20.0

    def effective_roots(self) -> list[Root]:
        return list(self.roots) or [Root("documents", self.path, legacy=True)]

    def root(self, name: str) -> Root:
        for r in self.effective_roots():
            if r.name == name:
                return r
        raise KeyError(f"unknown root '{name}' (known: {', '.join(r.name for r in self.effective_roots())})")

    def for_root(self, root: Root) -> "Config":
        """Config view for one root: its path and excludes (always excluding docsync's own dirs)."""
        ex = list(root.excludes if root.excludes is not None else self.excludes)
        if ".docsync*" not in ex:
            ex.append(".docsync*")
        return replace(self, path=root.path, excludes=ex, includes=root.includes)

    def peer_path(self, peer: "Peer", root: Root) -> str:
        return root.peer_path or (peer.path if root.legacy else root.path)

    def safetynet_policy(self) -> tuple[int, float | None, int | None]:
        return (self.safetynet_keep, self.safetynet_max_age_days, int(self.safetynet_max_gb * 1024**3))

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
        case_insensitive=bool(data.get("case_insensitive", True)),
        max_delete=int(data.get("max_delete", 25)),
        skip_on_battery=bool(data.get("skip_on_battery", False)),
        transfer_streams=max(1, int(data.get("transfer_streams", 4))),
        safetynet=bool(data.get("safetynet", True)),
        safetynet_keep=max(1, int(data.get("safetynet_keep", 5))),
        safetynet_max_age_days=float(data.get("safetynet_max_age_days", 14)),
        safetynet_max_gb=float(data.get("safetynet_max_gb", 20)),
    )
    raw_roots = data.get("roots", [])
    if raw_roots and "path" in data:  # explicit top-level path stays a root next to [[roots]]
        cfg.roots.append(Root("documents", cfg.path, legacy=True))
    for raw in raw_roots:
        if "name" not in raw or "path" not in raw:
            raise ValueError(f"root needs 'name' and 'path': {raw}")
        if not ROOT_NAME.match(str(raw["name"])):
            raise ValueError(f"root name must be letters, digits, - or _: {raw['name']!r}")
        cfg.roots.append(Root(str(raw["name"]), str(raw["path"]), str(raw.get("peer_path", "")),
                              [str(e) for e in raw["excludes"]] if "excludes" in raw else None,
                              includes=_includes(raw)))
    names = [r.name for r in cfg.roots]
    if len(names) != len(set(names)):
        raise ValueError(f"duplicate root names: {names}")
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


def _includes(raw: dict) -> list[str] | None:
    if "includes" not in raw:
        return None
    inc = raw["includes"]
    if not isinstance(inc, list) or not inc or not all(isinstance(p, str) and p.strip("/") for p in inc):
        raise ValueError(f"root '{raw['name']}': includes must be a non-empty list of strings")
    from docsync.manifest import compile_include  # manifest imports this module: import here
    for p in inc:
        try:
            compile_include(p)
        except re.error as e:
            raise ValueError(f"root '{raw['name']}': bad include pattern '{p}': {e}") from None
    return list(inc)


def load_config(file: Path = CONFIG_FILE) -> Config:
    if not file.exists():
        raise FileNotFoundError(f"no config at {file} — run: docsync init")
    return parse_config(file.read_text())


def _toml_str(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _toml_list(xs: list[str]) -> str:
    return "[" + ", ".join(_toml_str(x) for x in xs) + "]"


def render_config(cfg: Config) -> str:
    lines = [
        "# docsync configuration",
        f"path = {_toml_str(cfg.path)}",
        f"conflict_policy = {_toml_str(cfg.conflict_policy)}  # newest | local | remote",
        f"trash = {'true' if cfg.trash else 'false'}  # deletions go to ~/.Trash/docsync-*/",
        f"case_insensitive = {'true' if cfg.case_insensitive else 'false'}  # APFS: compare paths ignoring case",
        f"max_delete = {cfg.max_delete}  # refuse more deletions per run unless --force-deletes",
        f"skip_on_battery = {'true' if cfg.skip_on_battery else 'false'}  # launchd runs wait for AC power",
        f"transfer_streams = {cfg.transfer_streams}  # parallel rsync streams per peer (each its own ssh connection)",
        f"safetynet = {'true' if cfg.safetynet else 'false'}  # back up files before overwrite into <root>/.docsync-safetynet/",
        f"safetynet_keep = {cfg.safetynet_keep}",
        f"safetynet_max_age_days = {cfg.safetynet_max_age_days:g}",
        f"safetynet_max_gb = {cfg.safetynet_max_gb:g}",
        "excludes = [",
        *[f"  {_toml_str(e)}," for e in cfg.excludes],
        "]",
    ]
    for r in cfg.roots:
        if r.legacy:
            continue
        lines += ["", "[[roots]]", f"name = {_toml_str(r.name)}", f"path = {_toml_str(r.path)}"]
        if r.peer_path:
            lines.append(f"peer_path = {_toml_str(r.peer_path)}")
        if r.excludes is not None:
            lines.append(f"excludes = {_toml_list(r.excludes)}")
        if r.includes is not None:
            lines.append(f"includes = {_toml_list(r.includes)}")
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


def add_llm_roots(file: Path = CONFIG_FILE) -> list[str]:
    """Append the LLM_ROOTS missing from `file` as raw [[roots]] blocks; every existing byte stays.
    Returns the names added (empty: nothing to do, the file is untouched)."""
    if not file.exists():
        raise FileNotFoundError(f"no config at {file} — run: docsync init")
    text = file.read_text()
    data = tomllib.loads(text)
    if not data.get("roots") and "path" not in data:
        raise ValueError(f"{file} has no top-level `path`: add path = \"~/Documents\" first, "
                         "or the documents root would stop syncing once [[roots]] exist")
    have = {r.name for r in parse_config(text).effective_roots()}
    blocks, added = [], []
    for name, path, includes in LLM_ROOTS:
        if name in have:
            continue
        blocks.append("\n".join(["", "[[roots]]  # added by `docsync roots add-llm`", f"name = {_toml_str(name)}",
                                 f"path = {_toml_str(path)}",
                                 f"includes = {_toml_list(includes)}"]))
        added.append(name)
    if not added:
        return []
    new = text + ("" if text.endswith("\n") or not text else "\n") + "\n".join(blocks) + "\n"
    parse_config(new)  # never write a config that does not load
    tmp = file.with_suffix(".tmp")
    tmp.write_text(new)
    os.chmod(tmp, file.stat().st_mode & 0o777)  # a 0600 config stays 0600
    os.replace(tmp, file)
    return added


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
