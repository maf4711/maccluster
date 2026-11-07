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

SSH_OPTS = [  # control channel: listing, trash, rename — may ride on the user's ControlMaster
    "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", "-o", "StrictHostKeyChecking=accept-new",
    "-o", "Compression=no", "-o", "IPQoS=throughput",
]
# Bulk channel for rsync. Measured on Thunderbolt 80 Gb/s (2026-11-07): the user's default ssh
# (ControlMaster mux + Compression + chacha20) uploads at 40 MB/s; one plain aes128-gcm
# connection does 1,700 MB/s and four of them 4,000 MB/s. Never multiplex bulk transfers.
BULK_SSH_OPTS = SSH_OPTS + [
    "-o", "ControlMaster=no", "-o", "ControlPath=none",
    "-o", "Ciphers=aes128-gcm@openssh.com,aes256-gcm@openssh.com,chacha20-poly1305@openssh.com",
]
STREAM_MIN_FILES = 16          # below this a single rsync stream is used
STREAM_MIN_BYTES = 64 << 20


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
    out = {"files": 0, "bytes": 0, "sent": 0, "received": 0, "skipped": 0}
    for line in text.splitlines():
        for key, name in RSYNC_STAT_KEYS.items():
            if line.startswith(key + ":"):
                num = line.split(":", 1)[1].strip().split(" ")[0].replace(",", "")
                try:
                    out[name] = int(float(num))
                except ValueError:
                    pass
    return out


RETRY_DELAYS = (5, 15, 45)      # seconds between attempts on transport failures
RETRY_RCS = ("rc=255", "rc=30")  # 255: ssh/transport died, 30: timeout in data send/receive
_sleep = time.sleep              # patched in tests


def split_streams(paths: list[str], sizes: dict[str, int], n: int) -> list[list[str]]:
    """Greedy size-balanced split into up to n non-empty chunks (largest files first)."""
    n = max(1, min(n, len(paths)))
    if n == 1:
        return [list(paths)]
    order = sorted(paths, key=lambda p: -sizes.get(p, 0))
    chunks: list[list[str]] = [[] for _ in range(n)]
    load = [0] * n
    for p in order:
        i = load.index(min(load))
        chunks[i].append(p)
        load[i] += max(sizes.get(p, 0), 1)
    return [c for c in chunks if c]


def run_rsync_streams(src: str, dst: str, paths: list[str], sizes: dict[str, int], ssh_cmd: list[str] | None,
                      dry: bool, verbose: bool, progress=None, streams: int = 1) -> dict[str, int]:
    """Transfer `paths` with up to `streams` concurrent rsync processes (each its own ssh
    connection). Stats are summed; `progress(line, stream_idx)` gets every rsync progress line."""
    total_bytes = sum(sizes.get(p, 0) for p in paths)
    if streams <= 1 or dry or len(paths) < STREAM_MIN_FILES or total_bytes < STREAM_MIN_BYTES:
        return _run_rsync(src, dst, paths, ssh_cmd, dry, verbose, (lambda ln: progress(ln, 0)) if progress else None)
    from concurrent.futures import ThreadPoolExecutor
    chunks = split_streams(paths, sizes, streams)
    def one(i: int):
        cb = (lambda ln, _i=i: progress(ln, _i)) if progress else None
        return _run_rsync(src, dst, chunks[i], ssh_cmd, dry, verbose, cb)
    with ThreadPoolExecutor(max_workers=len(chunks)) as ex:
        results = list(ex.map(one, range(len(chunks))))
    total = {"files": 0, "bytes": 0, "sent": 0, "received": 0, "skipped": 0, "retries": 0, "errors": [], "streams": len(chunks)}
    for st in results:
        for k in ("files", "bytes", "sent", "received", "skipped", "retries"):
            total[k] += st.get(k, 0)
        total["errors"] += st.get("errors", [])
    return total


def _run_rsync(src: str, dst: str, paths: list[str], ssh_cmd: list[str] | None,
               dry: bool, verbose: bool, progress=None) -> dict[str, int]:
    """rsync with up to len(RETRY_DELAYS) retries when the transport itself fails (ssh banner
    timeout, peer rebooting, link flap, rsync timeout). Already transferred files are not redone.
    The returned stats carry "retries" = number of extra attempts."""
    retries = 0
    while True:
        try:
            st = _run_rsync_once(src, dst, paths, ssh_cmd, dry, verbose, progress)
            st["retries"] = retries
            return st
        except TransportError as e:
            if not any(rc in str(e) for rc in RETRY_RCS) or retries >= len(RETRY_DELAYS):
                raise
            _sleep(RETRY_DELAYS[retries])
            retries += 1


def _run_rsync_once(src: str, dst: str, paths: list[str], ssh_cmd: list[str] | None,
                    dry: bool, verbose: bool, progress=None) -> dict[str, int]:
    """rsync the given relative paths from src/ to dst/. Returns parsed --stats.
    `progress` is a callable fed with every rsync stdout line (for the progress bar)."""
    if not paths:
        return {"files": 0, "bytes": 0, "sent": 0, "received": 0, "skipped": 0}
    lst = _files_from(paths)
    try:
        # --partial-dir: an interrupted transfer never leaves a truncated file under its final
        # name (the next run would then see a "conflict"); .docsync-partial is in the default excludes.
        cmd = [rsync_bin(), "-a", "--from0", f"--files-from={lst}", "--partial-dir=.docsync-partial",
               "--no-human-readable", "--stats"]
        if progress is not None and not dry:
            cmd.append("--info=progress2")
        if verbose:
            cmd.append("-v")
        if dry:
            cmd.append("--dry-run")
        if ssh_cmd:
            cmd += ["-e", " ".join(shlex.quote(c) for c in ssh_cmd)]
        cmd += [src, dst]
        proc = subprocess.Popen(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                errors="replace", bufsize=1)
        out_lines: list[str] = []
        buf = ""
        while True:  # progress2 lines end in \r, stats lines in \n
            chunk = proc.stdout.read(512)
            if not chunk:
                break
            buf += chunk
            while True:
                i = min([j for j in (buf.find("\r"), buf.find("\n")) if j >= 0], default=-1)
                if i < 0:
                    break
                line, buf = buf[:i], buf[i + 1:]
                if progress is not None:
                    progress(line)
                if verbose and line and "%" not in line:
                    print(line)
                out_lines.append(line)
        if buf:
            out_lines.append(buf)
        stderr = proc.stderr.read()
        rc = proc.wait()
        stdout = "\n".join(out_lines)
        # 23/24: some files vanished or could not be read (iCloud/other sync in flight) — report, don't abort
        if rc not in (0, 23, 24):
            raise TransportError(f"rsync rc={rc}: {stderr.strip()[-800:]}")
        stats = parse_rsync_stats(stdout)
        if rc in (23, 24):
            errs = [l for l in stderr.splitlines() if "error:" in l or "failed:" in l]
            stats["skipped"] = max(len(errs), 1)
            stats["errors"] = errs[:5]
        return stats
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

    def manifest(self, excludes: list[str], dataless: set[str] | None = None) -> Manifest:
        return scan(self.root, excludes, dataless)

    def push(self, local_root: Path, paths: list[str], dry: bool, verbose: bool, progress=None,
             sizes: dict[str, int] | None = None, streams: int = 1) -> dict[str, int]:
        return run_rsync_streams(f"{local_root}/", f"{self.root}/", paths, sizes or {}, None, dry, verbose, progress, streams)

    def pull(self, local_root: Path, paths: list[str], dry: bool, verbose: bool, progress=None,
             sizes: dict[str, int] | None = None, streams: int = 1) -> dict[str, int]:
        return run_rsync_streams(f"{self.root}/", f"{local_root}/", paths, sizes or {}, None, dry, verbose, progress, streams)

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


def remote_path(path: str) -> str:
    """Shell expression for a peer path. '~/Documents' must become "$HOME"/Documents — a
    shlex-quoted '~/Documents' is a literal directory named '~' (which check() once created)."""
    if path == "~":
        return '"$HOME"'
    if path.startswith("~/"):
        return '"$HOME"/' + shlex.quote(path[2:])
    return shlex.quote(path)


def remote_remove_script(root: str, paths: list[str], stamp: str, use_trash: bool) -> str:
    """Shell script that trashes (or unlinks) paths below root on the peer.
    Quoting rule: every path is a single shlex-quoted token; "$T" is concatenated to it
    unquoted-adjacent ("$T"/'a b.gz'), never nested inside double quotes."""
    lines = ["set -e", f"cd {remote_path(root)}"]
    if use_trash:
        lines.append(f'T="$HOME/.Trash/docsync-{stamp}"')
    for p in paths:
        q = shlex.quote(p)
        if use_trash:
            d = shlex.quote(os.path.dirname(p) or ".")
            lines.append(f'mkdir -p "$T"/{d} && mv -f {q} "$T"/{q}')
        else:
            lines.append(f"rm -f {q}")
    return "\n".join(lines)


class RemoteTransport:
    """Peer reached over ssh; files move with rsync."""

    def __init__(self, userhost: str, path: str, bind: str | None = None):
        self.userhost = userhost
        self.path = path  # "~/Documents" or absolute
        self.ssh_cmd = ["ssh", *SSH_OPTS] + (["-b", bind] if bind else [])
        self.bulk_ssh_cmd = ["ssh", *BULK_SSH_OPTS] + (["-b", bind] if bind else [])
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
        rp = remote_path(self.path)
        res = self.ssh(f"mkdir -p {rp} && hostname -s && test -d {rp}", timeout=20)
        if res.returncode != 0:
            raise TransportError(res.stderr.strip().splitlines()[-1] if res.stderr.strip() else f"ssh rc={res.returncode}")
        return f"{res.stdout.strip()} ({(time.monotonic() - t0) * 1000:.0f} ms)"

    def manifest(self, excludes: list[str], dataless: set[str] | None = None) -> Manifest:
        q = remote_path(self.path)
        res = self.ssh(f"command -v python3 >/dev/null 2>&1 && python3 - {q}", stdin=LISTER_PY, timeout=600)
        if res.returncode != 0:
            res = self.ssh(LISTER_SH.format(root=q), timeout=600)
            if res.returncode != 0:
                raise TransportError(f"remote listing failed: {res.stderr.strip()[-400:]}")
        return parse_lines(res.stdout, excludes, dataless)

    def push(self, local_root: Path, paths: list[str], dry: bool, verbose: bool, progress=None,
             sizes: dict[str, int] | None = None, streams: int = 1) -> dict[str, int]:
        return run_rsync_streams(f"{local_root}/", self.rsync_path, paths, sizes or {}, self.bulk_ssh_cmd, dry, verbose, progress, streams)

    def pull(self, local_root: Path, paths: list[str], dry: bool, verbose: bool, progress=None,
             sizes: dict[str, int] | None = None, streams: int = 1) -> dict[str, int]:
        return run_rsync_streams(self.rsync_path, f"{local_root}/", paths, sizes or {}, self.bulk_ssh_cmd, dry, verbose, progress, streams)

    def remove(self, paths: list[str], stamp: str, use_trash: bool, dry: bool) -> None:
        if dry or not paths:
            return
        res = self.ssh(remote_remove_script(self.path, paths, stamp, use_trash), timeout=600)
        if res.returncode != 0:
            raise TransportError(f"remote delete failed: {res.stderr.strip()[-400:]}")

    def rename(self, src: str, dst: str, dry: bool) -> None:
        if dry:
            return
        cmd = (f"cd {remote_path(self.path)} && mkdir -p {shlex.quote(os.path.dirname(dst) or '.')} "
               f"&& mv -f {shlex.quote(src)} {shlex.quote(dst)}")
        res = self.ssh(cmd)
        if res.returncode != 0:
            raise TransportError(f"remote rename failed: {res.stderr.strip()[-400:]}")
