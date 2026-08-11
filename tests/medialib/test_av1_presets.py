from __future__ import annotations

import pytest

from psammophis.medialib import av1_presets as presets
from psammophis.medialib.svt import SvtImplementation


@pytest.mark.parametrize(
    ("height", "expected"),
    [
        (2160, "2160p"),
        (1600, "2160p"),
        (1599, "1080p"),
        (1080, "1080p"),
        (800, "1080p"),
        (799, "720p"),
        (720, "720p"),
        (540, "720p"),
        (539, "sd"),
        (480, "sd"),
        (0, "sd"),
    ],
)
def test_resolution_tier_boundaries(height, expected):
    assert presets.resolution_tier(height) == expected


def test_select_preset_sdr_matches_table():
    preset = presets.select_preset(1080, "film", hdr=False)
    assert preset is presets.PRESETS[("1080p", "film")]


def test_select_preset_hdr_uses_same_quality_target_as_sdr():
    base = presets.PRESETS[("2160p", "film")]
    hdr_preset = presets.select_preset(2160, "film", hdr=True)
    assert hdr_preset is base
    assert hdr_preset.crf == 20
    assert hdr_preset.nvenc_cq == 22


def test_every_film_grain_preset_enables_source_denoising():
    for preset in presets.PRESETS.values():
        if preset.film_grain:
            assert preset.film_grain_denoise


def test_svt_hdr_uses_its_own_crf_scale():
    preset = presets.PRESETS[("2160p", "film")]
    mainline = SvtImplementation("mainline", "v4.1.0", "test")
    fork = SvtImplementation("svt-av1-hdr", "v4.1.0", "test")
    assert presets.svt_crf(preset, mainline) == 20
    assert presets.svt_crf(preset, fork) == 27
    assert presets.svt_crf(preset, SvtImplementation("unknown", None, None)) is None


def test_select_preset_unknown_profile_raises():
    with pytest.raises(ValueError):
        presets.select_preset(1080, "documentary", hdr=False)


def test_select_preset_covers_every_tier_for_film_and_anime():
    for tier in ("2160p", "1080p", "720p", "sd"):
        for profile in ("film", "anime"):
            assert (tier, profile) in presets.PRESETS


def test_film_fast_is_calibrated_for_2160p_only():
    # See av1_presets.py's PROFILES comment: film-fast is only validated
    # against real encodes at 2160p so far, not extrapolated to other tiers.
    assert ("2160p", "film-fast") in presets.PRESETS
    for tier in ("1080p", "720p", "sd"):
        assert (tier, "film-fast") not in presets.PRESETS


def test_anime_fast_is_calibrated_for_2160p_only():
    # Same story as film-fast, above -- see av1_presets.py's PROFILES comment.
    assert ("2160p", "anime-fast") in presets.PRESETS
    for tier in ("1080p", "720p", "sd"):
        assert (tier, "anime-fast") not in presets.PRESETS


def test_select_preset_uncalibrated_tier_profile_raises_helpful_error():
    with pytest.raises(ValueError, match=r"film-fast.*1080p"):
        presets.select_preset(1080, "film-fast", hdr=False)
    with pytest.raises(ValueError, match=r"anime-fast.*1080p"):
        presets.select_preset(1080, "anime-fast", hdr=False)


def test_2160p_film_uses_preset_5_after_the_speed_sweep():
    # Raised from the historical default of 4 -- see notes/av1-encoding.md's
    # "Preset-speed investigation" for the real-encode evidence that 5 is
    # statistically indistinguishable from 4 in quality, just faster.
    assert presets.PRESETS[("2160p", "film")].svt_preset == 5


def test_2160p_anime_uses_preset_5_after_the_speed_sweep():
    # Raised from the historical default of 4 -- see notes/av1-encoding.md's
    # "Preset-speed investigation" for the Ghost in the Shell / Violet
    # Evergarden sweep that found 5 the best bits-per-quality of 5/6/7.
    assert presets.PRESETS[("2160p", "anime")].svt_preset == 5


def test_select_preset_film_fast_matches_2160p_film_quality_target():
    fast = presets.select_preset(2160, "film-fast", hdr=False)
    base = presets.PRESETS[("2160p", "film")]
    assert fast.svt_preset == 6
    assert fast.svt_preset != base.svt_preset
    # Only the CPU preset speed changes -- crf/tune/grain and every nvenc
    # field stay identical to 2160p-film (nvenc has no comparable knob).
    assert fast.crf == base.crf
    assert fast.svt_hdr_crf == base.svt_hdr_crf
    assert fast.svt_tune == base.svt_tune
    assert fast.film_grain == base.film_grain
    assert fast.nvenc_preset == base.nvenc_preset
    assert fast.nvenc_cq == base.nvenc_cq
    assert fast.nvenc_extra == base.nvenc_extra


def test_select_preset_anime_fast_matches_2160p_anime_quality_target():
    fast = presets.select_preset(2160, "anime-fast", hdr=False)
    base = presets.PRESETS[("2160p", "anime")]
    assert fast.svt_preset == 6
    assert fast.svt_preset != base.svt_preset
    # Only the CPU preset speed changes -- crf/tune/grain and every nvenc
    # field stay identical to 2160p-anime (nvenc has no comparable knob).
    assert fast.crf == base.crf
    assert fast.svt_hdr_crf == base.svt_hdr_crf
    assert fast.svt_tune == base.svt_tune
    assert fast.film_grain == base.film_grain
    assert fast.nvenc_preset == base.nvenc_preset
    assert fast.nvenc_cq == base.nvenc_cq
    assert fast.nvenc_extra == base.nvenc_extra


@pytest.mark.parametrize(
    ("channels", "expected_kbps"),
    [
        (1, 64),
        (2, 128),
        (6, 320),
        (8, 450),
    ],
)
def test_opus_bitrate_known_layouts(channels, expected_kbps):
    assert presets.opus_bitrate_kbps(channels) == expected_kbps


def test_opus_bitrate_unlisted_channel_count_interpolates_and_is_monotonic():
    values = [presets.opus_bitrate_kbps(c) for c in range(1, 13)]
    assert values == sorted(values)
    assert all(v <= presets._OPUS_MAX_KBPS for v in values)


def test_source_video_bitrate_prefers_video_stream_bit_rate():
    probed = {
        "video": {"bit_rate": 15_000_000},
        "format": {"bit_rate": 15_200_000, "duration": 100.0, "size": 190_000_000},
    }
    assert presets.source_video_bitrate_bps(probed) == 15_000_000


def test_source_video_bitrate_falls_back_to_format_bit_rate():
    probed = {"video": {}, "format": {"bit_rate": 15_200_000, "duration": 100.0, "size": 1}}
    assert presets.source_video_bitrate_bps(probed) == 15_200_000


def test_source_video_bitrate_falls_back_to_size_over_duration():
    probed = {"video": {}, "format": {"duration": 100.0, "size": 187_500_000}}
    assert presets.source_video_bitrate_bps(probed) == 15_000_000


def test_source_video_bitrate_none_when_nothing_available():
    assert presets.source_video_bitrate_bps({"video": {}, "format": {}}) is None


def test_max_bitrate_bps_applies_fraction():
    probed = {"video": {"bit_rate": 20_000_000}, "format": {}}
    assert presets.max_bitrate_bps(probed, fraction=0.5) == 10_000_000


def test_max_bitrate_bps_none_when_source_unknown():
    assert presets.max_bitrate_bps({"video": {}, "format": {}}) is None


def test_max_bitrate_bps_none_when_fraction_is_zero_or_negative():
    probed = {"video": {"bit_rate": 20_000_000}, "format": {}}
    assert presets.max_bitrate_bps(probed, fraction=0) is None
    assert presets.max_bitrate_bps(probed, fraction=-1) is None
