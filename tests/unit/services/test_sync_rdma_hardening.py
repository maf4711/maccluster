"""sync_rdma hardening: argv safety, manifest rel safety, parser and runner robustness."""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable, Sequence
from pathlib import Path

import pytest

from maccluster.constants import TIMEOUT_SYNC
from maccluster.errors import CliError
from maccluster.services.sync_rdma import (
    manifest_lines,
    run_rdma_transfer,
    xfer_subprocess_runner,
)
from maccluster.services.sync_service import FileMeta
from maccluster.services.transport_ladder import TransportFailed

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


# --- stdout parsing --------------------------------------------------------------------


def test_run_survives_garbage_and_partial_json():
    seen: list[tuple[int, int]] = []
    fake = FakeXfer(
        [
            "{",
            '{"event":',
            '{"event":"progress","done":1,"total":',
            "\x00\x01\x02",
            "{" * 3000,
            '{"event":"progress","done":' + "[" * 50_000 + "]" * 50_000 + "}",
            "[1,2,3]",
            '"str"',
            "null",
            '{"event":"progress","done":4,"total":10}',
            '{"event":"done","bytes":10}',
        ]
    )
    assert _run(fake, on_progress=lambda d, t: seen.append((d, t))) == 10
    assert seen == [(4, 10)]


def test_run_ignores_nonfinite_and_negative_numbers():
    seen: list[tuple[int, int]] = []
    fake = FakeXfer(
        [
            '{"event":"progress","done":NaN,"total":10}',
            '{"event":"progress","done":Infinity,"total":10}',
            '{"event":"progress","done":1e400,"total":10}',
            '{"event":"progress","done":-5,"total":10}',
            '{"event":"progress","done":2,"total":-1}',
            '{"event":"progress","done":3,"total":NaN}',
            '{"event":"done","bytes":-1}',
        ]
    )
    assert _run(fake, on_progress=lambda d, t: seen.append((d, t))) == 3
    assert seen == [(2, 2), (3, 3)]


def test_run_error_reason_is_sanitized_and_capped():
    reason = "\x1b[31mred\x1b[0m " + "x" * 5000 + "\n\tend"
    fake = FakeXfer([json.dumps({"event": "error", "reason": reason})])
    with pytest.raises(TransportFailed) as ei:
        _run(fake)
    assert "\x1b" not in ei.value.reason
    assert "\n" not in ei.value.reason
    assert "red" in ei.value.reason
    assert len(ei.value.reason) <= 400


def test_run_stderr_tail_is_sanitized():
    fake = FakeXfer([], rc=2, stderr="\x1b]0;evil\x07link down\x00")
    with pytest.raises(TransportFailed) as ei:
        _run(fake)
    assert "link down" in ei.value.reason
    assert "\x1b" not in ei.value.reason and "\x00" not in ei.value.reason


def test_run_progress_callback_error_becomes_transport_failed():
    fake = FakeXfer(['{"event":"progress","done":1,"total":2}'])

    def bad(done: int, total: int) -> None:
        raise RuntimeError("bar broke")

    with pytest.raises(TransportFailed) as ei:
        _run(fake, on_progress=bad)
    assert "bar broke" in ei.value.reason


# --- partial flag / timeout defaults ------------------------------------------------------


def test_run_failure_after_spawn_is_marked_partial():
    with pytest.raises(TransportFailed) as rc_fail:
        _run(FakeXfer([], rc=3, stderr="boom"))
    assert rc_fail.value.partial is True
    with pytest.raises(TransportFailed) as ev_fail:
        _run(FakeXfer(['{"event":"error","reason":"peer aborted"}']))
    assert ev_fail.value.partial is True
    with pytest.raises(TransportFailed) as to_fail:
        _run(FakeXfer([], rc=124))
    assert to_fail.value.partial is True

    def broken(argv, *, stdin_text, on_line, timeout):
        raise CliError("tool not found: arep", exit_code=1)

    with pytest.raises(TransportFailed) as spawn_fail:
        _run(broken)  # type: ignore[arg-type]
    assert spawn_fail.value.partial is False


@pytest.mark.parametrize("timeout", [0, -1.0])
def test_run_non_positive_timeout_falls_back_to_default(timeout: float):
    fake = FakeXfer([json.dumps({"event": "done", "bytes": 10})])
    _run(fake, timeout=timeout)
    assert fake.timeout == TIMEOUT_SYNC


# --- default runner: process hygiene ---------------------------------------------------------


def _script(tmp_path: Path, body: str) -> str:
    script = tmp_path / "arep"
    script.write_text("#!/bin/sh\n" + body)
    script.chmod(0o755)
    return str(script)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _wait_gone(pid: int, seconds: float = 5.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if not _alive(pid):
            return True
        time.sleep(0.05)
    return not _alive(pid)


def test_default_runner_caps_overlong_lines(tmp_path: Path):
    script = _script(
        tmp_path,
        "head -c 3000000 /dev/zero | tr '\\0' 'x'\n"
        "echo\n"
        'echo "{\\"event\\":\\"done\\",\\"bytes\\":7}"\n',
    )
    lines: list[str] = []
    rc, _ = xfer_subprocess_runner([script], stdin_text="", on_line=lines.append, timeout=30.0)
    assert rc == 0
    assert max(len(line) for line in lines) < 1_000_000
    assert json.loads(lines[-1])["bytes"] == 7


def test_default_runner_kills_child_when_on_line_raises(tmp_path: Path):
    pid_file = tmp_path / "pid"
    script = _script(tmp_path, f"echo $$ > {pid_file}\necho hello\nsleep 20\n")

    def explode(_line: str) -> None:
        raise RuntimeError("consumer broke")

    t0 = time.monotonic()
    with pytest.raises(RuntimeError):
        xfer_subprocess_runner([script], stdin_text="", on_line=explode, timeout=30.0)
    assert time.monotonic() - t0 < 10.0
    pid = int(pid_file.read_text().strip())
    assert _wait_gone(pid)


def test_default_runner_timeout_kills_grandchildren(tmp_path: Path):
    pid_file = tmp_path / "pid"
    script = _script(tmp_path, f"sh -c 'echo $$ > {pid_file}; sleep 8' &\nwait\n")
    t0 = time.monotonic()
    rc, _ = xfer_subprocess_runner([script], stdin_text="", on_line=lambda s: None, timeout=0.5)
    assert rc == 124
    assert time.monotonic() - t0 < 4.0
    assert _wait_gone(int(pid_file.read_text().strip()))


def test_default_runner_encodes_manifest_before_spawn(tmp_path: Path):
    pid_file = tmp_path / "pid"
    script = _script(tmp_path, f"echo $$ > {pid_file}\ncat > /dev/null\nexit 0\n")
    with pytest.raises((CliError, ValueError)):
        xfer_subprocess_runner(
            [script], stdin_text="bad \udcff surrogate\n", on_line=lambda s: None, timeout=5.0
        )
    assert not pid_file.exists()
