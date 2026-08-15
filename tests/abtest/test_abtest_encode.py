from __future__ import annotations

from pathlib import Path

from psammophis.abtest.encode import encode_command, extract_command
from psammophis.abtest.spec import ClipWindow, ResolvedVariant
from psammophis.medialib.svt import SvtImplementation

_HDR = SvtImplementation("svt-av1-hdr", "v4.1.0", "test")


def test_extract_command_is_ffv1_video_only():
    window = ClipWindow("c01_t055", 55.0, 20.0, "test")
    cmd = extract_command(Path("/src.mkv"), Path("/ref.mkv"), window)
    assert cmd[0] == "ffmpeg"
    assert "-ss" in cmd
    assert "55.000" in cmd
    assert "-c:v" in cmd
    assert cmd[cmd.index("-c:v") + 1] == "ffv1"
    assert "-an" in cmd


def test_encode_omits_film_grain_when_zero():
    variant = ResolvedVariant("g0", 26, 5, 0, 0, False, "label")
    cmd = encode_command(Path("/ref.mkv"), Path("/enc.mkv"), variant, _HDR, None)
    params = cmd[cmd.index("-svtav1-params") + 1]
    tokens = dict(part.split("=", 1) for part in params.split(":") if "=" in part)
    assert cmd[cmd.index("-crf") + 1] == "26"
    assert "film-grain" not in tokens
    assert tokens["tune"] == "0"


def test_encode_emits_grain_and_denoise():
    variant = ResolvedVariant("old", 27, 5, 0, 10, True, "label")
    cmd = encode_command(Path("/ref.mkv"), Path("/enc.mkv"), variant, _HDR, None)
    params = cmd[cmd.index("-svtav1-params") + 1]
    assert "film-grain=10" in params
    assert "film-grain-denoise=1" in params


def test_tune5_does_not_pin_conflicting_fork_extras():
    variant = ResolvedVariant("t5", 30, 2, 5, 0, False, "label")
    cmd = encode_command(Path("/ref.mkv"), Path("/enc.mkv"), variant, _HDR, "bt709")
    params = cmd[cmd.index("-svtav1-params") + 1]
    tokens = dict(part.split("=", 1) for part in params.split(":") if "=" in part)
    assert tokens["tune"] == "5"
    assert "tf-strength" not in tokens
    assert "ac-bias" not in tokens
    assert cmd[cmd.index("-preset") + 1] == "2"
