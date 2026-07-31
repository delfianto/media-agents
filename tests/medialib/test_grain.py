from __future__ import annotations

import subprocess
from pathlib import Path

from psammophis.medialib import grain


class _FakeCompleted:
    def __init__(self, returncode=0, stderr=""):
        self.returncode = returncode
        self.stdout = ""
        self.stderr = stderr


# A real (trimmed) ssim filter summary line, the shape `_measure_sample`
# parses -- captured directly from `ffmpeg -filter_complex
# "[0:v]format=yuv420p,split=2[a][b];[b]hqdn3d=6:4:6:4[den];[a][den]ssim"`
# against a real file in this library.
_REAL_SSIM_STDERR = (
    "[Parsed_ssim_3 @ 0x7fae34004a40] SSIM Y:0.980157 (17.023863) "
    "U:0.993599 (21.937587) V:0.998203 (27.453644) All:0.985405 (18.357888)\n"
)


def test_sample_offsets_spreads_across_duration():
    offsets = grain._sample_offsets(100.0, (0.2, 0.5, 0.8), sample_duration=2.0)
    assert offsets == [20.0, 50.0, 80.0]


def test_sample_offsets_clamps_so_sample_fits_before_end():
    # A fraction landing within sample_duration of the end must not push the
    # sample window past duration.
    offsets = grain._sample_offsets(10.0, (0.95,), sample_duration=2.0)
    assert offsets == [8.0]


def test_sample_offsets_clamps_short_file_to_zero():
    offsets = grain._sample_offsets(1.0, (0.5,), sample_duration=2.0)
    assert offsets == [0.0]


def test_measure_sample_parses_all_ssim_value(monkeypatch):
    def fake_run(cmd, **kwargs):
        assert cmd[0] == "ffmpeg"
        return _FakeCompleted(returncode=0, stderr=_REAL_SSIM_STDERR)

    monkeypatch.setattr(subprocess, "run", fake_run)
    score = grain._measure_sample(Path("in.mkv"), 10.0, 2.0)
    assert score is not None
    assert score == 1.0 - 0.985405


def test_measure_sample_returns_none_when_ssim_line_missing(monkeypatch):
    def fake_run(cmd, **kwargs):
        return _FakeCompleted(returncode=0, stderr="no ssim here\n")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert grain._measure_sample(Path("in.mkv"), 0.0, 2.0) is None


def test_measure_sample_returns_none_on_missing_ffmpeg(monkeypatch):
    def fake_run(cmd, **kwargs):
        raise FileNotFoundError("no ffmpeg")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert grain._measure_sample(Path("in.mkv"), 0.0, 2.0) is None


def test_measure_sample_returns_none_on_timeout(monkeypatch):
    def fake_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, 60)

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert grain._measure_sample(Path("in.mkv"), 0.0, 2.0) is None


def test_measure_grain_returns_none_when_duration_missing():
    assert grain.measure_grain("in.mkv", None) is None
    assert grain.measure_grain("in.mkv", 0) is None
    assert grain.measure_grain("in.mkv", -1) is None


def test_measure_grain_averages_successful_samples(monkeypatch):
    scores = iter([0.010, 0.020, 0.030])
    monkeypatch.setattr(grain, "_measure_sample", lambda path, offset, duration: next(scores))
    result = grain.measure_grain("in.mkv", 100.0)
    assert result is not None
    assert result.samples == (0.010, 0.020, 0.030)
    assert result.score == 0.020


def test_measure_grain_skips_failed_samples(monkeypatch):
    scores = iter([0.010, None, 0.030])
    monkeypatch.setattr(grain, "_measure_sample", lambda path, offset, duration: next(scores))
    result = grain.measure_grain("in.mkv", 100.0)
    assert result is not None
    assert result.samples == (0.010, 0.030)
    assert result.score == 0.020


def test_measure_grain_returns_none_when_every_sample_fails(monkeypatch):
    monkeypatch.setattr(grain, "_measure_sample", lambda path, offset, duration: None)
    assert grain.measure_grain("in.mkv", 100.0) is None
