import json
from dataclasses import replace
from pathlib import Path

from psammophis.compare import metrics
from psammophis.compare.metrics import _frame_filter, find_vmaf_model, run_vmaf_clip
from psammophis.compare.probe import VideoInfo

_BASE_VIDEO_INFO = VideoInfo(
    path="ref.mkv",
    duration=10.0,
    size=1000,
    bit_rate=1000,
    width=3840,
    height=2160,
    frame_rate=24000 / 1001,
    frame_count=240,
    pix_fmt="yuv420p10le",
    color_primaries="bt2020",
    color_transfer="smpte2084",
    color_space="bt2020nc",
    color_range="tv",
)


def test_frame_filter_pads_sdr_to_nominal_size():
    info = replace(_BASE_VIDEO_INFO, color_transfer="bt709")
    graph = _frame_filter(info)
    assert graph.endswith("pad=w=3840:h=2160:x=(ow-iw)/2:y=(oh-ih)/2:color=black")


def test_frame_filter_pads_hdr_to_nominal_size():
    assert _BASE_VIDEO_INFO.is_hdr
    graph = _frame_filter(_BASE_VIDEO_INFO)
    assert graph.endswith("pad=w=3840:h=2160:x=(ow-iw)/2:y=(oh-ih)/2:color=black")


def test_standard_model_for_1080p():
    model, name = find_vmaf_model(1080)
    assert model == "version=vmaf_v0.6.1"
    assert name == "vmaf_v0.6.1"


def test_4k_model_is_selected_when_installed(monkeypatch):
    monkeypatch.setattr(Path, "is_file", lambda self: str(self).startswith("/usr/share"))
    model, name = find_vmaf_model(2160)
    assert model == "/usr/share/model/vmaf_4k_v0.6.1.json"
    assert name == "vmaf_4k_v0.6.1"


def test_vmaf_rebuilds_matching_cfr_timestamps_from_frame_index(monkeypatch, tmp_path):
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        log_path.write_text(json.dumps({"frames": [{"frameNum": 1, "metrics": {"vmaf": 95}}]}))

        class Result:
            returncode = 0
            tail = ""

        return Result()

    monkeypatch.setattr(metrics, "_run_vmaf_process", fake_run)
    log_path = tmp_path / "vmaf.json"
    frames = run_vmaf_clip(
        Path("reference.mkv"),
        Path("distorted.mkv"),
        10.0,
        2.0,
        "version=vmaf_v0.6.1",
        4,
        log_path,
        24000 / 1001,
        3840,
        2160,
    )
    graph = captured["cmd"][captured["cmd"].index("-filter_complex") + 1]
    assert graph.count("setpts=N/(23.976023976024*TB)") == 2
    assert frames[0]["timestamp"] == 10.0 + 1001 / 24000


def test_vmaf_pads_both_streams_to_nominal_size(monkeypatch, tmp_path):
    # Some anime BD encodes crop cinematic-aspect scenes to a smaller coded
    # picture height instead of baking in letterbox bars, which otherwise
    # makes libvmaf refuse the clip with "input height must match".
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        log_path.write_text(json.dumps({"frames": [{"frameNum": 1, "metrics": {"vmaf": 95}}]}))

        class Result:
            returncode = 0
            tail = ""

        return Result()

    monkeypatch.setattr(metrics, "_run_vmaf_process", fake_run)
    log_path = tmp_path / "vmaf.json"
    run_vmaf_clip(
        Path("reference.mkv"),
        Path("distorted.mkv"),
        0.0,
        2.0,
        "version=vmaf_v0.6.1",
        4,
        log_path,
        24000 / 1001,
        3840,
        2160,
    )
    graph = captured["cmd"][captured["cmd"].index("-filter_complex") + 1]
    assert graph.count("pad=w=3840:h=2160:x=(ow-iw)/2:y=(oh-ih)/2:color=black") == 2
