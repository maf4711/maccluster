"""RDMA rung: manifest to `arep xfer`, JSON-Lines progress, exit → TransportFailed."""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence

import pytest

from maccluster.services.sync_rdma import (
    manifest_lines,
    manifest_text,
    run_rdma_transfer,
)
from maccluster.services.sync_service import FileMeta
from maccluster.services.transport_ladder import TransportFailed

INV = {
    "Documents/a.txt": FileMeta(mtime_ns=1_700_000_000_123_456_789, size=10),
    "Developer/x/y.bin": FileMeta(mtime_ns=5, size=2_000_000),
    "Desktop/ünï.md": FileMeta(mtime_ns=7, size=0),
}


class FakeXfer:
    """Scripted `arep xfer` stand-in: records argv + stdin, replays stdout lines."""

    def __init__(self, lines: list[str], rc: int = 0, stderr: str = "") -> None:
        self.lines = lines
        self.rc = rc
        self.stderr = stderr
        self.argv: tuple[str, ...] = ()
        self.stdin = ""

    def __call__(
        self,
        argv: Sequence[str],
        *,
        stdin_text: str,
        on_line: Callable[[str], None],
        timeout: float,
    ) -> tuple[int, str]:
        self.argv = tuple(argv)
        self.stdin = stdin_text
        for line in self.lines:
            on_line(line)
        return self.rc, self.stderr


def _ev(**kw) -> str:
    return json.dumps(kw)


# --- manifest -------------------------------------------------------------------


def test_manifest_lines_are_json_lines_with_exact_keys():
    lines = list(manifest_lines(["Documents/a.txt", "Developer/x/y.bin"], INV))
    assert len(lines) == 2
    assert "\n" not in "".join(lines)
    first = json.loads(lines[0])
    assert first == {"rel": "Documents/a.txt", "size": 10, "mtimeNs": 1_700_000_000_123_456_789}
    assert list(first) == ["rel", "size", "mtimeNs"]
    assert json.loads(lines[1]) == {"rel": "Developer/x/y.bin", "size": 2_000_000, "mtimeNs": 5}


def test_manifest_preserves_order_and_unicode_and_is_lazy():
    gen = manifest_lines(["Desktop/ünï.md", "Documents/a.txt"], INV)
    assert next(gen).startswith('{"rel": "Desktop/ünï.md"')
    assert "Desktop/ünï.md" in manifest_text(["Desktop/ünï.md"], INV)
    assert list(manifest_lines([], INV)) == []


def test_manifest_text_ends_each_line_with_newline():
    text = manifest_text(["Documents/a.txt", "Desktop/ünï.md"], INV)
    assert text.endswith("\n")
    assert text.count("\n") == 2
    assert [json.loads(row)["rel"] for row in text.splitlines()] == [
        "Documents/a.txt",
        "Desktop/ünï.md",
    ]


def test_manifest_unknown_rel_raises_keyerror():
    with pytest.raises(KeyError):
        list(manifest_lines(["nope"], INV))


# --- run_rdma_transfer: argv + stdin ----------------------------------------------


@pytest.mark.parametrize("direction", ["push", "pull"])
def test_run_spawns_arep_xfer_with_manifest_on_stdin(direction: str):
    fake = FakeXfer([_ev(event="done", bytes=10)])
    out = run_rdma_transfer(
        node_id="node-b",
        direction=direction,
        rels=["Documents/a.txt"],
        inv=INV,
        arep_bin="/Users/x/.local/bin/arep",
        on_progress=lambda d, t: None,
        runner=fake,
    )
    assert out == 10
    assert fake.argv == (
        "/Users/x/.local/bin/arep",
        "xfer",
        direction,
        "--node",
        "node-b",
        "--manifest",
        "-",
    )
    assert fake.stdin == manifest_text(["Documents/a.txt"], INV)


def test_run_rejects_bad_direction():
    with pytest.raises(ValueError):
        run_rdma_transfer(
            node_id="node-b",
            direction="sideways",  # type: ignore[arg-type]
            rels=[],
            inv=INV,
            arep_bin="arep",
            on_progress=lambda d, t: None,
            runner=FakeXfer([]),
        )


def test_run_empty_rels_does_not_spawn():
    fake = FakeXfer([_ev(event="done", bytes=999)])
    out = run_rdma_transfer(
        node_id="node-b",
        direction="push",
        rels=[],
        inv=INV,
        arep_bin="arep",
        on_progress=lambda d, t: None,
        runner=fake,
    )
    assert out == 0
    assert fake.argv == ()


# --- progress parsing -----------------------------------------------------------------


def test_run_forwards_progress_events_and_returns_done_bytes():
    seen: list[tuple[int, int]] = []
    fake = FakeXfer(
        [
            "arep xfer: starting",  # human noise is ignored
            _ev(event="progress", done=0, total=2_000_010),
            "",
            _ev(event="progress", done=1_000_000, total=2_000_010),
            _ev(event="other", foo=1),
            _ev(event="progress", done=2_000_010, total=2_000_010),
            _ev(event="done", bytes=2_000_010),
        ]
    )
    out = run_rdma_transfer(
        node_id="node-b",
        direction="push",
        rels=["Documents/a.txt", "Developer/x/y.bin"],
        inv=INV,
        arep_bin="arep",
        on_progress=lambda d, t: seen.append((d, t)),
        runner=fake,
    )
    assert out == 2_000_010
    assert seen == [(0, 2_000_010), (1_000_000, 2_000_010), (2_000_010, 2_000_010)]


def test_run_without_done_event_falls_back_to_last_progress():
    fake = FakeXfer([_ev(event="progress", done=7, total=10)])
    out = run_rdma_transfer(
        node_id="node-b",
        direction="pull",
        rels=["Documents/a.txt"],
        inv=INV,
        arep_bin="arep",
        on_progress=lambda d, t: None,
        runner=fake,
    )
    assert out == 7


def test_run_ignores_malformed_progress_values():
    seen: list[tuple[int, int]] = []
    fake = FakeXfer(
        [
            _ev(event="progress", done="x", total=10),
            _ev(event="progress", done=3),
            _ev(event="done", bytes="many"),
        ]
    )
    out = run_rdma_transfer(
        node_id="node-b",
        direction="push",
        rels=["Documents/a.txt"],
        inv=INV,
        arep_bin="arep",
        on_progress=lambda d, t: seen.append((d, t)),
        runner=fake,
    )
    assert out == 3
    assert seen == [(3, 3)]


# --- failures → TransportFailed --------------------------------------------------------


def test_run_nonzero_exit_raises_transport_failed_with_stderr():
    fake = FakeXfer([_ev(event="progress", done=1, total=2)], rc=3, stderr="qp: link down\n")
    with pytest.raises(TransportFailed) as ei:
        run_rdma_transfer(
            node_id="node-b",
            direction="push",
            rels=["Documents/a.txt"],
            inv=INV,
            arep_bin="arep",
            on_progress=lambda d, t: None,
            runner=fake,
        )
    assert ei.value.transport == "rdma"
    assert "exit 3" in ei.value.reason
    assert "link down" in ei.value.reason


def test_run_error_event_raises_even_on_zero_exit():
    fake = FakeXfer(
        [_ev(event="error", reason="peer refused manifest"), _ev(event="done", bytes=0)]
    )
    with pytest.raises(TransportFailed) as ei:
        run_rdma_transfer(
            node_id="node-b",
            direction="pull",
            rels=["Documents/a.txt"],
            inv=INV,
            arep_bin="arep",
            on_progress=lambda d, t: None,
            runner=fake,
        )
    assert ei.value.transport == "rdma"
    assert ei.value.reason == "peer refused manifest"


def test_run_error_event_reason_wins_over_exit_code():
    fake = FakeXfer([_ev(event="error", reason="no rdma device to peer")], rc=2, stderr="boom")
    with pytest.raises(TransportFailed) as ei:
        run_rdma_transfer(
            node_id="node-b",
            direction="push",
            rels=["Documents/a.txt"],
            inv=INV,
            arep_bin="arep",
            on_progress=lambda d, t: None,
            runner=fake,
        )
    assert ei.value.reason == "no rdma device to peer"


def test_run_timeout_exit_code_is_reported_as_timeout():
    fake = FakeXfer([], rc=124)
    with pytest.raises(TransportFailed) as ei:
        run_rdma_transfer(
            node_id="node-b",
            direction="push",
            rels=["Documents/a.txt"],
            inv=INV,
            arep_bin="arep",
            on_progress=lambda d, t: None,
            runner=fake,
            timeout=5.0,
        )
    assert "timeout" in ei.value.reason


def test_run_runner_exception_becomes_transport_failed():
    from maccluster.errors import CliError

    def broken(argv, *, stdin_text, on_line, timeout):
        raise CliError("tool not found: arep", exit_code=1)

    with pytest.raises(TransportFailed) as ei:
        run_rdma_transfer(
            node_id="node-b",
            direction="push",
            rels=["Documents/a.txt"],
            inv=INV,
            arep_bin="arep",
            on_progress=lambda d, t: None,
            runner=broken,
        )
    assert "arep" in ei.value.reason


def test_run_manifest_keyerror_surfaces_before_spawn():
    fake = FakeXfer([_ev(event="done", bytes=1)])
    with pytest.raises(KeyError):
        run_rdma_transfer(
            node_id="node-b",
            direction="push",
            rels=["missing"],
            inv=INV,
            arep_bin="arep",
            on_progress=lambda d, t: None,
            runner=fake,
        )
    assert fake.argv == ()


# --- default runner (real subprocess, python stand-in for arep) ---------------------------


def test_default_runner_streams_stdin_and_lines(tmp_path):
    from maccluster.services.sync_rdma import xfer_subprocess_runner

    script = tmp_path / "arep"
    script.write_text(
        "#!/bin/sh\n"
        'n=$(wc -l < /dev/stdin | tr -d " ")\n'
        'echo "{\\"event\\":\\"progress\\",\\"done\\":$n,\\"total\\":$n}"\n'
        'echo "{\\"event\\":\\"done\\",\\"bytes\\":$n}"\n'
        "echo warn >&2\n"
        "exit 0\n"
    )
    script.chmod(0o755)
    lines: list[str] = []
    rc, err = xfer_subprocess_runner(
        [str(script), "xfer", "push", "--node", "n", "--manifest", "-"],
        stdin_text="a\nb\nc\n",
        on_line=lines.append,
        timeout=20.0,
    )
    assert rc == 0
    assert "warn" in err
    assert [json.loads(row)["event"] for row in lines] == ["progress", "done"]
    assert json.loads(lines[1])["bytes"] == 3


def test_default_runner_rejects_non_arep_basename(tmp_path):
    from maccluster.errors import CliError
    from maccluster.services.sync_rdma import xfer_subprocess_runner

    with pytest.raises(CliError):
        xfer_subprocess_runner(
            ["/bin/sh", "-c", "true"], stdin_text="", on_line=lambda s: None, timeout=5.0
        )


def test_default_runner_timeout_returns_124(tmp_path):
    from maccluster.services.sync_rdma import xfer_subprocess_runner

    script = tmp_path / "arep"
    script.write_text("#!/bin/sh\nsleep 5\n")
    script.chmod(0o755)
    rc, _err = xfer_subprocess_runner(
        [str(script)], stdin_text="", on_line=lambda s: None, timeout=0.5
    )
    assert rc == 124
