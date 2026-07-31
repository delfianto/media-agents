import json
from pathlib import Path

from psammophis.compare import metrics
from psammophis.compare.metrics import find_vmaf_model, run_vmaf_clip


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
    )
    graph = captured["cmd"][captured["cmd"].index("-filter_complex") + 1]
    assert graph.count("setpts=N/(23.976023976024*TB)") == 2
    assert frames[0]["timestamp"] == 10.0 + 1001 / 24000
