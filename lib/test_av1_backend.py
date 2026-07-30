from __future__ import annotations

import pytest
from medialib import av1_backend


def _video(**overrides):
    video = {
        "color_transfer": "smpte2084",
        "dolby_vision": None,
        "hdr10_plus": None,
    }
    video.update(overrides)
    return video


# --- choose_backend: pre-existing DV/HDR10+/explicit-backend behavior ---


def test_choose_backend_explicit_cpu_always_wins():
    assert av1_backend.choose_backend(_video(), "cpu", gpu_index=0) == "cpu"


def test_choose_backend_explicit_nvenc_without_gpu_raises():
    with pytest.raises(ValueError):
        av1_backend.choose_backend(_video(), "nvenc", gpu_index=None)


def test_choose_backend_explicit_nvenc_dv_without_nvencc_raises():
    video = _video(dolby_vision={"dv_profile": 8})
    with pytest.raises(ValueError):
        av1_backend.choose_backend(video, "nvenc", gpu_index=0, nvencc_ok=False)


def test_choose_backend_explicit_nvenc_dv_with_nvencc_ok():
    video = _video(dolby_vision={"dv_profile": 8})
    assert av1_backend.choose_backend(video, "nvenc", gpu_index=0, nvencc_ok=True) == "nvenc"


def test_choose_backend_auto_no_gpu_is_cpu():
    assert av1_backend.choose_backend(_video(), "auto", gpu_index=None) == "cpu"


def test_choose_backend_auto_dv_without_nvencc_falls_back_to_cpu():
    video = _video(dolby_vision={"dv_profile": 8})
    assert av1_backend.choose_backend(video, "auto", gpu_index=0, nvencc_ok=False) == "cpu"


def test_choose_backend_auto_dv_with_nvencc_stays_nvenc():
    video = _video(dolby_vision={"dv_profile": 8})
    assert av1_backend.choose_backend(video, "auto", gpu_index=0, nvencc_ok=True) == "nvenc"


def test_choose_backend_auto_plain_hdr_gpu_available_is_nvenc():
    assert av1_backend.choose_backend(_video(), "auto", gpu_index=0, nvencc_ok=False) == "nvenc"


# --- choose_backend: the new grain-aware tie-breaker ---


def test_choose_backend_auto_grain_below_threshold_stays_nvenc():
    backend = av1_backend.choose_backend(
        _video(), "auto", gpu_index=0, nvencc_ok=False, grain_score=0.005, grain_threshold=0.012
    )
    assert backend == "nvenc"


def test_choose_backend_auto_grain_at_or_above_threshold_prefers_cpu():
    backend = av1_backend.choose_backend(
        _video(), "auto", gpu_index=0, nvencc_ok=False, grain_score=0.012, grain_threshold=0.012
    )
    assert backend == "cpu"
    backend = av1_backend.choose_backend(
        _video(), "auto", gpu_index=0, nvencc_ok=False, grain_score=0.020, grain_threshold=0.012
    )
    assert backend == "cpu"


def test_choose_backend_grain_score_none_is_ignored():
    # No measurement taken -- must behave identically to the pre-grain
    # default (nvenc whenever a GPU is available), not treat "unknown" as
    # "clean" or "grainy".
    backend = av1_backend.choose_backend(
        _video(), "auto", gpu_index=0, nvencc_ok=False, grain_score=None
    )
    assert backend == "nvenc"


def test_choose_backend_grain_never_overrides_explicit_backend():
    # Grain can only ever push auto's nvenc -> cpu; it must not affect an
    # explicit --backend choice in either direction.
    assert (
        av1_backend.choose_backend(_video(), "nvenc", gpu_index=0, nvencc_ok=True, grain_score=0.9)
        == "nvenc"
    )
    assert av1_backend.choose_backend(_video(), "cpu", gpu_index=0, grain_score=0.0) == "cpu"


def test_choose_backend_grain_never_forces_cpu_to_nvenc():
    # cpu stays the safe fallback: a low grain score must not push a
    # DV-forced-cpu decision back onto nvenc.
    video = _video(dolby_vision={"dv_profile": 8})
    backend = av1_backend.choose_backend(
        video, "auto", gpu_index=0, nvencc_ok=False, grain_score=0.0
    )
    assert backend == "cpu"


# --- choose_encode_engine (moved unchanged from av1transcode.run) ---


def test_choose_encode_engine_plain_sdr_uses_ffmpeg():
    assert av1_backend.choose_encode_engine("nvenc", _video(dolby_vision=None), nvencc_ok=True) == (
        "ffmpeg"
    )


def test_choose_encode_engine_dv_nvenc_uses_nvencc_when_available():
    video = _video(dolby_vision={"dv_profile": 8})
    assert av1_backend.choose_encode_engine("nvenc", video, nvencc_ok=True) == "nvencc"


def test_choose_encode_engine_hdr10_plus_nvenc_without_nvencc_falls_back_to_ffmpeg():
    video = _video(hdr10_plus={"side_data_type": "HDR10+"})
    assert av1_backend.choose_encode_engine("nvenc", video, nvencc_ok=False) == "ffmpeg"


def test_choose_encode_engine_cpu_backend_always_ffmpeg():
    video = _video(dolby_vision={"dv_profile": 8})
    assert av1_backend.choose_encode_engine("cpu", video, nvencc_ok=True) == "ffmpeg"


# --- grain_routing_applies ---


def test_grain_routing_applies_false_for_explicit_backend():
    assert av1_backend.grain_routing_applies("cpu", _video(), gpu_index=0, nvencc_ok=True) is False
    assert (
        av1_backend.grain_routing_applies("nvenc", _video(), gpu_index=0, nvencc_ok=True) is False
    )


def test_grain_routing_applies_false_without_gpu():
    assert av1_backend.grain_routing_applies("auto", _video(), gpu_index=None, nvencc_ok=True) is (
        False
    )


def test_grain_routing_applies_false_when_dv_already_forces_cpu():
    video = _video(dolby_vision={"dv_profile": 8})
    assert av1_backend.grain_routing_applies("auto", video, gpu_index=0, nvencc_ok=False) is False


def test_grain_routing_applies_true_for_plain_auto_with_gpu():
    assert av1_backend.grain_routing_applies("auto", _video(), gpu_index=0, nvencc_ok=False) is True


def test_grain_routing_applies_true_for_dv_with_nvencc_available():
    # nvencc can carry DV on the GPU path, so grain is still a meaningful
    # tie-breaker here (cpu vs nvenc/nvencc), unlike the no-nvencc case.
    video = _video(dolby_vision={"dv_profile": 8})
    assert av1_backend.grain_routing_applies("auto", video, gpu_index=0, nvencc_ok=True) is True
