from __future__ import annotations

import sys

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
