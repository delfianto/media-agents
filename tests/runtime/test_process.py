from __future__ import annotations

import signal
import subprocess
import sys
import threading
import time

import pytest

from psammophis.runtime.process import (
    ProcessSupervisor,
    make_ffmpeg_progress_parser,
    parse_ffmpeg_progress_line,
    parse_nvencc_progress_line,
)


def test_ffmpeg_progress_parser_normal_and_end():
    state: dict = {}
    assert parse_ffmpeg_progress_line("stdout", "frame=48", state=state) is None
    assert parse_ffmpeg_progress_line("stdout", "out_time_us=1958333", state=state) is None
    assert parse_ffmpeg_progress_line("stdout", "fps=24.5", state=state) is None
    assert parse_ffmpeg_progress_line("stdout", "speed=1.5x", state=state) is None
    result = parse_ffmpeg_progress_line("stdout", "progress=continue", state=state)
    assert result is not None
    assert abs(float(result["media_position"] or 0) - 1.958333) < 1e-6
    assert result["fps"] == 24.5
    assert result["speed"] == 1.5
    assert result["progress"] == "continue"


def test_ffmpeg_parser_with_duration_percent():
    parser = make_ffmpeg_progress_parser(total_duration=10.0)
    parser("stdout", "out_time_us=5000000")
    parser("stdout", "speed=2.0x")
    result = parser("stdout", "progress=continue")
    assert result is not None
    assert result["percent"] == 50.0
    assert result["eta_seconds"] == 2.5


def test_ffmpeg_unknown_duration():
    parser = make_ffmpeg_progress_parser(total_duration=None)
    parser("stdout", "out_time_us=1000000")
    result = parser("stdout", "progress=end")
    assert result is not None
    assert "percent" not in result or result.get("percent") is None


def test_nvencc_progress_line():
    line = (
        "[45.7%] 62 frames: 313.13 fps, 198 kbps, remain 0:00:12, "
        "GPU 18%, VE 0%, est out size 0.1MB"
    )
    result = parse_nvencc_progress_line("stdout", line)
    assert result is not None
    assert result["percent"] == 45.7
    assert result["fps"] == 313.13
    assert result["eta_seconds"] == 12


def test_nvencc_unknown_format_returns_none():
    assert parse_nvencc_progress_line("stdout", "encoded 48 frames, 170.21 fps") is None


def test_supervisor_drains_noisy_stderr(tmp_path):
    log_path = tmp_path / "proc.log"
    # Child floods stderr then exits 0 — must not deadlock.
    script = (
        "import sys\nfor i in range(5000):\n    print('noise', i, file=sys.stderr)\nprint('done')\n"
    )
    result = ProcessSupervisor(
        [sys.executable, "-c", script],
        log_path=log_path,
        min_progress_interval=0.0,
    ).run()
    assert result.returncode == 0
    assert log_path.is_file()
    assert "noise" in log_path.read_text()
    assert "done" in log_path.read_text()


def test_supervisor_nonzero_exit(tmp_path):
    result = ProcessSupervisor(
        [sys.executable, "-c", "import sys; sys.exit(7)"],
        log_path=tmp_path / "x.log",
    ).run()
    assert result.returncode == 7


def test_supervisor_timeout_terminates_the_child_group():
    started = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        ProcessSupervisor(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            timeout=0.1,
            cancellation_grace=0.2,
        ).run()
    assert time.monotonic() - started < 3


@pytest.mark.parametrize("link_kind", ["symlink", "hardlink"])
def test_supervisor_never_truncates_a_linked_log_target(tmp_path, link_kind):
    protected = tmp_path / "protected.mkv"
    log_path = tmp_path / "encode.log"
    protected.write_bytes(b"irreplaceable")
    if link_kind == "symlink":
        log_path.symlink_to(protected)
    else:
        log_path.hardlink_to(protected)

    with pytest.raises(OSError, match="refusing"):
        ProcessSupervisor([sys.executable, "-c", "print('never runs')"], log_path=log_path).run()

    assert protected.read_bytes() == b"irreplaceable"


def test_carriage_return_progress_is_delivered_before_process_exit(tmp_path):
    seen = threading.Event()
    results = []
    supervisor = ProcessSupervisor(
        [
            sys.executable,
            "-c",
            "import sys,time; sys.stdout.write('[50.0%] 10 frames: 2.0 fps, 100 kbps, "
            "remain 0:00:10\\r'); sys.stdout.flush(); time.sleep(30)",
        ],
        log_path=tmp_path / "cr.log",
        progress_parser=parse_nvencc_progress_line,
        on_progress=lambda _progress: seen.set(),
        min_progress_interval=0.0,
        cancellation_grace=0.2,
    )
    thread = threading.Thread(target=lambda: results.append(supervisor.run()))
    thread.start()
    assert seen.wait(3), "carriage-return progress was buffered until EOF"
    assert thread.is_alive()
    supervisor.cancel()
    thread.join(3)
    assert not thread.is_alive()
    assert results[0].cancelled
    assert "[50.0%]" in (tmp_path / "cr.log").read_text()


def test_heartbeat_continues_after_child_closes_its_output_streams():
    heartbeat = threading.Event()
    supervisor = ProcessSupervisor(
        [
            sys.executable,
            "-c",
            "import os,time; os.close(1); os.close(2); time.sleep(30)",
        ],
        on_heartbeat=heartbeat.set,
        heartbeat_interval=0.05,
        cancellation_grace=0.2,
    )
    results = []
    thread = threading.Thread(target=lambda: results.append(supervisor.run()))
    thread.start()
    assert heartbeat.wait(3), "closed output streams suppressed liveness heartbeats"
    supervisor.cancel()
    thread.join(3)
    assert not thread.is_alive()
    assert results[0].cancelled


def test_callback_failure_cannot_deadlock_reader_threads(tmp_path):
    script = (
        "import signal,sys,time\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "print('trigger', flush=True)\n"
        "time.sleep(30)\n"
    )

    def fail(_stream, _line):
        raise ValueError("consumer broke")

    started = time.monotonic()
    with pytest.raises(RuntimeError, match="stream consumer failed"):
        ProcessSupervisor(
            [sys.executable, "-c", script],
            log_path=tmp_path / "failure.log",
            on_line=fail,
            cancellation_grace=0.2,
        ).run()
    assert time.monotonic() - started < 3


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX process-group behavior")
def test_cancel_reaches_descendant_process_group(tmp_path):
    ready_path = tmp_path / "child-ready"
    terminated_path = tmp_path / "child-terminated"
    child_code = (
        "import signal,sys,time,pathlib\n"
        f"ready=pathlib.Path({str(ready_path)!r})\n"
        f"terminated=pathlib.Path({str(terminated_path)!r})\n"
        "def stop(_sig, _frame):\n"
        "    terminated.write_text('yes')\n"
        "    sys.exit(0)\n"
        "signal.signal(signal.SIGTERM, stop)\n"
        "ready.write_text('yes')\n"
        "time.sleep(30)\n"
    )
    parent_code = (
        "import signal,subprocess,sys,time,pathlib\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        f"subprocess.Popen([sys.executable, '-c', {child_code!r}])\n"
        f"ready=pathlib.Path({str(ready_path)!r})\n"
        "while not ready.exists(): time.sleep(0.01)\n"
        "print('ready', flush=True)\n"
        "time.sleep(30)\n"
    )
    ready = threading.Event()
    results = []
    supervisor = ProcessSupervisor(
        [sys.executable, "-c", parent_code],
        on_line=lambda _stream, line: ready.set() if line == "ready" else None,
        cancellation_grace=0.3,
    )
    thread = threading.Thread(target=lambda: results.append(supervisor.run()))
    thread.start()
    assert ready.wait(3)
    supervisor.cancel(signal.SIGTERM)
    thread.join(4)
    assert not thread.is_alive()
    assert results[0].cancelled
    assert terminated_path.read_text() == "yes"
