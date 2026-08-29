"""sync_rdma hardening: argv safety, manifest rel safety, parser and runner robustness."""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from pathlib import Path

import pytest

from maccluster.errors import CliError
from maccluster.services.sync_rdma import (
    manifest_lines,
    run_rdma_transfer,
    xfer_subprocess_runner,
)
from maccluster.services.sync_service import FileMeta

INV = {
    "Documents/a.txt": FileMeta(mtime_ns=1, size=10),
    "Developer/x/y.bin": FileMeta(mtime_ns=5, size=20),
}


class FakeXfer:
    def __init__(self, lines: list[str], rc: int = 0, stderr: str = "") -> None:
        self.lines, self.rc, self.stderr = lines, rc, stderr
        self.argv: tuple[str, ...] = ()
        self.timeout: float | None = None

    def __call__(
        self,
        argv: Sequence[str],
        *,
        stdin_text: str,
        on_line: Callable[[str], None],
        timeout: float,
    ) -> tuple[int, str]:
        self.argv, self.timeout = tuple(argv), timeout
        for line in self.lines:
            on_line(line)
        return self.rc, self.stderr


def _run(fake: FakeXfer, **kw) -> int:
    args = dict(
        node_id="node-b",
        direction="push",
        rels=["Documents/a.txt"],
        inv=INV,
        arep_bin="arep",
        on_progress=lambda d, t: None,
        runner=fake,
    )
    args.update(kw)
    return run_rdma_transfer(**args)


# --- argv: node id / binary ----------------------------------------------------------


@pytest.mark.parametrize(
    "node_id",
    ["", "-x", "--manifest", "node b", "node;b", "node\nb", "nöde", "a/b", "a\x00b", "a\tb"],
)
def test_run_rejects_unsafe_node_id_before_spawn(node_id: str):
    fake = FakeXfer([json.dumps({"event": "done", "bytes": 1})])
    with pytest.raises(ValueError):
        _run(fake, node_id=node_id)
    assert fake.argv == ()


@pytest.mark.parametrize("node_id", ["node-b", "mac.mini_1", "A1", "x" * 64])
def test_run_accepts_plain_node_ids(node_id: str):
    fake = FakeXfer([json.dumps({"event": "done", "bytes": 10})])
    assert _run(fake, node_id=node_id) == 10
    assert fake.argv[4] == node_id


@pytest.mark.parametrize(
    "arep_bin",
    ["", "../arep", "bin/arep", "arep;sh", "arep ", "/usr/local/bin/notarep", "/x/arep\x00"],
)
def test_run_rejects_unsafe_binary_before_spawn(arep_bin: str):
    fake = FakeXfer([json.dumps({"event": "done", "bytes": 1})])
    with pytest.raises(ValueError):
        _run(fake, arep_bin=arep_bin)
    assert fake.argv == ()


def test_run_accepts_bare_name_and_absolute_arep_path():
    fake = FakeXfer([json.dumps({"event": "done", "bytes": 10})])
    assert _run(fake, arep_bin="/Users/x/.local/bin/arep") == 10
    assert fake.argv[0] == "/Users/x/.local/bin/arep"


def test_default_runner_rejects_relative_arep_path(tmp_path: Path):
    with pytest.raises(CliError):
        xfer_subprocess_runner(
            ["sub/arep", "xfer"], stdin_text="", on_line=lambda s: None, timeout=5.0
        )


# --- manifest: rel safety ------------------------------------------------------------


@pytest.mark.parametrize(
    "rel",
    [
        "/etc/passwd",
        "../x",
        "a/../b",
        "a/..",
        "..",
        "a\x00b",
        "a\nb",
        "a\x1b[31mb",
        "a\x7fb",
        "",
        ".",
        "a/./b",
        "a//b",
        "a/",
        "a\udcffb",
    ],
)
def test_manifest_rejects_unsafe_rel(rel: str):
    inv = {rel: FileMeta(mtime_ns=1, size=1)}
    with pytest.raises(ValueError):
        list(manifest_lines([rel], inv))


def test_manifest_rejects_non_string_rel():
    inv = {42: FileMeta(mtime_ns=1, size=1)}  # type: ignore[dict-item]
    with pytest.raises(ValueError):
        list(manifest_lines([42], inv))  # type: ignore[list-item]


def test_manifest_accepts_unicode_spaces_and_dotfiles():
    inv = {
        "Desktop/ünï cödé.md": FileMeta(mtime_ns=1, size=1),
        ".ssh/config": FileMeta(mtime_ns=1, size=1),
        "a.b/c-d_e": FileMeta(mtime_ns=1, size=1),
    }
    assert len(list(manifest_lines(list(inv), inv))) == 3


def test_run_unsafe_rel_never_spawns():
    fake = FakeXfer([json.dumps({"event": "done", "bytes": 1})])
    inv = {"../escape": FileMeta(mtime_ns=1, size=1)}
    with pytest.raises(ValueError):
        _run(fake, rels=["../escape"], inv=inv)
    assert fake.argv == ()
