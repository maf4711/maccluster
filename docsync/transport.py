"""Transports: how to list, copy, rename and remove files on a peer.

RemoteTransport = ssh + rsync.  LocalTransport = a directory on this machine
(a mounted volume, or the test-suite's second tree) — no ssh involved.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from docsync.manifest import LISTER_PY, LISTER_SH, Manifest, parse_lines, scan

SSH_OPTS = [
    "-o", "BatchMode=yes", "-o", "ConnectTimeout=6", "-o", "StrictHostKeyChecking=accept-new",
    "-o", "Compression=no", "-o", "IPQoS=throughput",
]


def rsync_bin() -> str:
    for cand in ("/opt/homebrew/bin/rsync", "/usr/local/bin/rsync"):
        if os.access(cand, os.X_OK):
            return cand
    return shutil.which("rsync") or "rsync"


def trash_dir(home: Path, stamp: str) -> Path:
    return home / ".Trash" / f"docsync-{stamp}"


class TransportError(RuntimeError):
    pass


def _files_from(paths: list[str]) -> str:
    fd, name = tempfile.mkstemp(prefix="docsync-files-")
    with os.fdopen(fd, "w") as fh:
        fh.write("\0".join(paths) + ("\0" if paths else ""))
    return name


RSYNC_STAT_KEYS = {
    "Number of regular files transferred": "files",
    "Total transferred file size": "bytes",
    "Total bytes sent": "sent",
    "Total bytes received": "received",
}


def parse_rsync_stats(text: str) -> dict[str, int]:
    """Pull the numbers docsync reports from `rsync --stats` output."""
    out = {"files": 0, "bytes": 0, "sent": 0, "received": 0}
    for line in text.splitlines():
        for key, name in RSYNC_STAT_KEYS.items():
            if line.startswith(key + ":"):
                num = line.split(":", 1)[1].strip().split(" ")[0].replace(",", "")
                try:
                    out[name] = int(float(num))
                except ValueError:
                    pass
    return out


def _run_rsync(src: str, dst: str, paths: list[str], ssh_cmd: list[str] | None,
               dry: bool, verbose: bool) -> dict[str, int]:
    """rsync the given relative paths from src/ to dst/. Returns parsed --stats."""
    if not paths:
        return {"files": 0, "bytes": 0, "sent": 0, "received": 0}
    lst = _files_from(paths)
    try:
        cmd = [rsync_bin(), "-a", "--from0", f"--files-from={lst}", "--partial",
               "--no-human-readable", "--stats"]
        if verbose:
            cmd.append("-v")
        if dry:
            cmd.append("--dry-run")
        if ssh_cmd:
            cmd += ["-e", " ".join(shlex.quote(c) for c in ssh_cmd)]
        cmd += [src, dst]
        res = subprocess.run(cmd, text=True, capture_output=True)
        if verbose and res.stdout:
            print(res.stdout.rstrip())
        if res.returncode not in (0, 24):  # 24 = vanished source files, tolerable
            raise TransportError(f"rsync rc={res.returncode}: {res.stderr.strip()[-800:]}")
        return parse_rsync_stats(res.stdout)
    finally:
        os.unlink(lst)


class LocalTransport:
    """Peer is a directory on this machine."""

    def __init__(self, root: Path, home: Path | None = None):
        self.root = Path(root).expanduser()
        self.home = home or self.root.parent
        self.label = str(self.root)

    def check(self) -> str:
        if not self.root.is_dir():
            raise TransportError(f"not a directory: {self.root}")
        return self.label

    def manifest(self, excludes: list[str]) -> Manifest:
        return scan(self.root, excludes)

    def push(self, local_root: Path, paths: list[str], dry: bool, verbose: bool) -> dict[str, int]:
        return _run_rsync(f"{local_root}/", f"{self.root}/", paths, None, dry, verbose)

    def pull(self, local_root: Path, paths: list[str], dry: bool, verbose: bool) -> dict[str, int]:
        return _run_rsync(f"{self.root}/", f"{local_root}/", paths, None, dry, verbose)

    def remove(self, paths: list[str], stamp: str, use_trash: bool, dry: bool) -> None:
        remove_local(self.root, self.home, paths, stamp, use_trash, dry)

    def rename(self, src: str, dst: str, dry: bool) -> None:
        if dry:
            return
        (self.root / dst).parent.mkdir(parents=True, exist_ok=True)
        os.replace(self.root / src, self.root / dst)


def remove_local(root: Path, home: Path, paths: list[str], stamp: str, use_trash: bool, dry: bool) -> None:
    if dry:
        return
    for p in paths:
        full = root / p
        if not full.exists():
            continue
        if use_trash:
            dst = trash_dir(home, stamp) / p
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(full), str(dst))
        else:
            full.unlink()


class RemoteTransport:
    """Peer reached over ssh; files move with rsync."""

    def __init__(self, userhost: str, path: str, bind: str | None = None):
        self.userhost = userhost
        self.path = path  # "~/Documents" or absolute
        self.ssh_cmd = ["ssh", *SSH_OPTS] + (["-b", bind] if bind else [])
        self.label = f"{userhost}:{path}"

    # rsync wants "host:Documents/" (relative to the login home) or "host:/abs/".
    @property
    def rsync_path(self) -> str:
        p = self.path
        if p.startswith("~/"):
            p = p[2:]
        elif p == "~":
            p = "."
        return f"{self.userhost}:{shlex.quote(p)}/"

    def ssh(self, remote_cmd: str, stdin: str | None = None, timeout: int = 120) -> subprocess.CompletedProcess:
        return subprocess.run([*self.ssh_cmd, self.userhost, remote_cmd], input=stdin,
                              text=True, capture_output=True, timeout=timeout)

    def check(self) -> str:
        t0 = time.monotonic()
        res = self.ssh(f"mkdir -p {shlex.quote(self.path)} && hostname -s && test -d {shlex.quote(self.path)}", timeout=20)
        if res.returncode != 0:
            raise TransportError(res.stderr.strip().splitlines()[-1] if res.stderr.strip() else f"ssh rc={res.returncode}")
        return f"{res.stdout.strip()} ({(time.monotonic() - t0) * 1000:.0f} ms)"

    def manifest(self, excludes: list[str]) -> Manifest:
        q = shlex.quote(self.path)
        res = self.ssh(f"command -v python3 >/dev/null 2>&1 && python3 - {q}", stdin=LISTER_PY, timeout=600)
        if res.returncode != 0:
            res = self.ssh(LISTER_SH.format(root=q), timeout=600)
            if res.returncode != 0:
                raise TransportError(f"remote listing failed: {res.stderr.strip()[-400:]}")
        return parse_lines(res.stdout, excludes)

    def push(self, local_root: Path, paths: list[str], dry: bool, verbose: bool) -> dict[str, int]:
        return _run_rsync(f"{local_root}/", self.rsync_path, paths, self.ssh_cmd, dry, verbose)

    def pull(self, local_root: Path, paths: list[str], dry: bool, verbose: bool) -> dict[str, int]:
        return _run_rsync(self.rsync_path, f"{local_root}/", paths, self.ssh_cmd, dry, verbose)

    def remove(self, paths: list[str], stamp: str, use_trash: bool, dry: bool) -> None:
        if dry or not paths:
            return
        script = ["set -e", f"cd {shlex.quote(self.path)}"]
        if use_trash:
            script.append(f"T=\"$HOME/.Trash/docsync-{stamp}\"")
        for p in paths:
            q = shlex.quote(p)
            if use_trash:
                script.append(f'mkdir -p "$T/{shlex.quote(os.path.dirname(p) or ".")}" && mv -f {q} "$T/{q}"')
            else:
                script.append(f"rm -f {q}")
        res = self.ssh("\n".join(script), timeout=600)
        if res.returncode != 0:
            raise TransportError(f"remote delete failed: {res.stderr.strip()[-400:]}")

    def rename(self, src: str, dst: str, dry: bool) -> None:
        if dry:
            return
        cmd = (f"cd {shlex.quote(self.path)} && mkdir -p {shlex.quote(os.path.dirname(dst) or '.')} "
               f"&& mv -f {shlex.quote(src)} {shlex.quote(dst)}")
        res = self.ssh(cmd)
        if res.returncode != 0:
            raise TransportError(f"remote rename failed: {res.stderr.strip()[-400:]}")
