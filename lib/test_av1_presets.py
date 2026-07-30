from __future__ import annotations

import pytest
from medialib import av1_presets as presets


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


def test_select_preset_hdr_lowers_crf_and_cq_without_mutating_table():
    base = presets.PRESETS[("2160p", "film")]
    hdr_preset = presets.select_preset(2160, "film", hdr=True)
    assert hdr_preset.crf == base.crf - presets.HDR_QUALITY_BONUS
    assert hdr_preset.nvenc_cq == base.nvenc_cq - presets.HDR_QUALITY_BONUS
    # HDR selection must not mutate the shared table entry other callers read.
    assert base.crf == presets.PRESETS[("2160p", "film")].crf


def test_select_preset_unknown_profile_raises():
    with pytest.raises(ValueError):
        presets.select_preset(1080, "documentary", hdr=False)


def test_select_preset_covers_every_tier_and_profile():
    for tier in ("2160p", "1080p", "720p", "sd"):
        for profile in presets.PROFILES:
            assert (tier, profile) in presets.PRESETS


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
