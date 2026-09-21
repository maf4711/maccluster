"""Real child processes exercise buffering, deadlines and error propagation."""

import sys
import time
from pathlib import Path

import pytest

from maccluster.adapters.process import ProcessRunner


@pytest.fixture
def runner():
    return ProcessRunner(allowlist=frozenset({Path(sys.executable).name}))


def test_stream_input_drains_output_and_stderr(runner, tmp_path):
    payload = b"abc" * 100_000
    source = tmp_path / "input"
    source.write_bytes(payload)
    progress = []
    result = runner.stream_stdin_file(
        [
            sys.executable,
            "-c",
            "import sys; sys.stderr.write('e'*200000); sys.stdout.buffer.write(sys.stdin.buffer.read())",
        ],
        input_path=source,
        timeout=5,
        on_progress=lambda a, b: progress.append((a, b)),
    )
    assert result.returncode == 0
    assert result.stdout.encode() == payload
    assert result.stderr == "e" * 200_000
    assert progress[-1] == (len(payload), len(payload))


def test_stream_output_drains_stderr(runner, tmp_path):
    dest = tmp_path / "output"
    result = runner.stream_stdout_file(
        [
            sys.executable,
            "-c",
            "import sys; sys.stderr.write('e'*200000); sys.stdout.write('x'*200000)",
        ],
        output_path=dest,
        timeout=5,
    )
    assert result.returncode == 0
    assert dest.read_bytes() == b"x" * 200_000
    assert result.stderr == "e" * 200_000


@pytest.mark.parametrize("direction", ["stdin", "stdout"])
def test_stream_deadline_stops_silent_child(runner, tmp_path, direction):
    path = tmp_path / "data"
    path.write_bytes(b"x" * 200_000)
    started = time.monotonic()
    method = getattr(runner, f"stream_{direction}_file")
    result = method(
        [sys.executable, "-c", "import time; time.sleep(10)"],
        **{"input_path" if direction == "stdin" else "output_path": path},
        timeout=0.15,
    )
    assert result.returncode == 124 and result.timed_out
    assert time.monotonic() - started < 3


def test_pipe_deadline_includes_producer_exit(runner):
    result = runner.run_pipe(
        [sys.executable, "-c", "import os,time; os.close(1); time.sleep(10)"],
        [sys.executable, "-c", "import sys; sys.stdin.buffer.read()"],
        timeout=0.15,
    )
    assert result.returncode == 124 and result.timed_out


def test_stream_deadline_stops_descendants_holding_stderr(runner, tmp_path):
    started = time.monotonic()
    result = runner.stream_stdout_file(
        [
            sys.executable,
            "-c",
            "import subprocess,sys,time; subprocess.Popen([sys.executable,'-c','import time; time.sleep(10)']); time.sleep(10)",
        ],
        output_path=tmp_path / "out",
        timeout=0.2,
    )
    assert result.timed_out
    assert time.monotonic() - started < 3
