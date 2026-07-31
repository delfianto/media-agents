from __future__ import annotations

from psammophis.analyze.report import (
    analysis_to_dict,
    build_analysis,
    classify_dynamic_range,
    format_analysis,
)
from psammophis.medialib.grain import GrainMeasurement
from psammophis.medialib.svt import SvtImplementation

_MAINLINE = SvtImplementation("mainline", "v4.1.0", "test")


def _build_analysis(*args, **kwargs):
    return build_analysis(*args, **kwargs, svt_implementation=_MAINLINE)


def _video(**overrides):
    video = {
        "index": 0,
        "codec_name": "hevc",
        "profile": "Main 10",
        "width": 3840,
        "height": 2160,
        "pix_fmt": "yuv420p10le",
        "bit_rate": 60_000_000,
        "color_primaries": "bt2020",
        "color_transfer": "smpte2084",
        "color_space": "bt2020nc",
        "color_range": "tv",
        "mastering_display": None,
        "content_light": None,
        "dolby_vision": None,
        "hdr10_plus": None,
    }
    video.update(overrides)
    return video


def _probed(video, *, size=45_000_000_000, duration=9326.6):
    return {"video": video, "format": {"size": size, "duration": duration}}


def test_classify_dynamic_range_dolby_vision_wins_over_hdr10_plus_and_hdr10():
    assert classify_dynamic_range(_video(dolby_vision={"dv_profile": 8})) == "Dolby Vision"


def test_classify_dynamic_range_hdr10_plus():
    assert classify_dynamic_range(_video(hdr10_plus={"side_data_type": "HDR10+"})) == "HDR10+"


def test_classify_dynamic_range_static_hdr10():
    assert classify_dynamic_range(_video()) == "HDR10"


def test_classify_dynamic_range_sdr():
    assert classify_dynamic_range(_video(color_transfer="bt709")) == "SDR"


def test_build_analysis_picks_tier_and_preset_from_height_and_profile():
    probed = _probed(_video(height=2160))
    a = _build_analysis("Movie.mkv", probed, "film", gpu_index=0, nvencc_ok=False, grain=None)
    assert a.tier == "2160p"
    assert a.preset.name == "2160p-film"


def test_build_analysis_no_gpu_forces_cpu():
    probed = _probed(_video())
    a = _build_analysis("Movie.mkv", probed, "film", gpu_index=None, nvencc_ok=False, grain=None)
    assert a.backend == "cpu"
    assert a.backend_error is None


def test_build_analysis_grain_at_or_above_threshold_prefers_cpu():
    probed = _probed(_video(color_transfer="bt709"))  # plain SDR, no DV forcing
    grain = GrainMeasurement(score=0.02, samples=(0.02,))
    a = _build_analysis(
        "Movie.mkv",
        probed,
        "film",
        gpu_index=0,
        nvencc_ok=False,
        grain=grain,
        grain_threshold=0.012,
    )
    assert a.backend == "cpu"
    assert a.grain is grain


def test_build_analysis_grain_below_threshold_stays_nvenc():
    probed = _probed(_video(color_transfer="bt709"))
    grain = GrainMeasurement(score=0.005, samples=(0.005,))
    a = _build_analysis(
        "Movie.mkv",
        probed,
        "film",
        gpu_index=0,
        nvencc_ok=False,
        grain=grain,
        grain_threshold=0.012,
    )
    assert a.backend == "nvenc"


def test_format_analysis_includes_key_facts():
    probed = _probed(_video())
    grain = GrainMeasurement(score=0.015, samples=(0.014, 0.016))
    a = _build_analysis(
        "Some Movie.mkv",
        probed,
        "film",
        gpu_index=0,
        nvencc_ok=True,
        grain=grain,
        grain_threshold=0.012,
    )
    text = format_analysis(a)
    assert "Some Movie.mkv" in text
    assert "2160p-film" in text
    assert "backend: cpu via" in text
    assert "gpu_index=0" in text
    assert "grain: 0.0150" in text
    assert "cpu preferred" in text


def test_format_analysis_omits_grain_line_when_not_measured():
    probed = _probed(_video())
    a = _build_analysis("Movie.mkv", probed, "film", gpu_index=None, nvencc_ok=False, grain=None)
    assert "grain:" not in format_analysis(a)


def test_analysis_to_dict_round_trips_key_fields():
    probed = _probed(_video(height=1080, color_transfer="bt709"))
    grain = GrainMeasurement(score=0.02, samples=(0.02,))
    a = _build_analysis("Movie.mkv", probed, "anime", gpu_index=0, nvencc_ok=False, grain=grain)
    d = analysis_to_dict(a)
    assert d["path"] == "Movie.mkv"
    assert d["resolution_tier"] == "1080p"
    assert d["preset"] == "1080p-anime"
    assert d["grain_score"] == 0.02
    assert d["grain_samples"] == [0.02]
    assert d["svt_implementation"] == "mainline"
    assert d["svt_crf"] == 25


def test_analysis_to_dict_grain_fields_none_when_not_measured():
    probed = _probed(_video())
    a = _build_analysis("Movie.mkv", probed, "film", gpu_index=None, nvencc_ok=False, grain=None)
    d = analysis_to_dict(a)
    assert d["grain_score"] is None
    assert d["grain_samples"] is None
