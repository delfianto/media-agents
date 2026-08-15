from __future__ import annotations

import pytest

from psammophis.medialib import av1_presets as presets
from psammophis.medialib.sourceclass import (
    STREAM_CEILING_BPS,
    apply_source_class,
    classify_source,
)


def _probed(
    *,
    codec: str = "hevc",
    profile: str | None = "Main 10",
    width: int = 3840,
    height: int = 2160,
    pix_fmt: str = "yuv420p10le",
    bit_rate: int | None = 60_000_000,
    format_bit_rate: int | None = None,
) -> dict:
    return {
        "video": {
            "codec_name": codec,
            "profile": profile,
            "width": width,
            "height": height,
            "pix_fmt": pix_fmt,
            "bit_rate": bit_rate,
        },
        "format": {"bit_rate": format_bit_rate, "duration": None, "size": None},
    }


def test_uhd_hevc_remux_is_master():
    c = classify_source(_probed())
    assert c.name == "master"
    assert c.override is False
    assert "master floor" in c.reason


def test_wowgirls_style_4k_avc_is_webrip():
    c = classify_source(
        _probed(
            codec="h264",
            profile="High",
            pix_fmt="yuv420p",
            bit_rate=21_000_000,
        )
    )
    assert c.name == "webrip"
    assert "between stream" in c.reason


def test_constrained_baseline_is_stream_even_above_lowest():
    c = classify_source(
        _probed(
            codec="h264",
            profile="Constrained Baseline",
            width=1920,
            height=1080,
            pix_fmt="yuv420p",
            bit_rate=5_000_000,
        )
    )
    assert c.name == "stream"
    assert "Baseline" in c.reason


def test_low_bitrate_480p_commercial_is_stream_not_lowest():
    c = classify_source(
        _probed(
            codec="h264",
            profile="High",
            width=854,
            height=480,
            pix_fmt="yuv420p",
            bit_rate=1_010_000,
        )
    )
    assert c.name == "stream"
    assert c.video_bps == 1_010_000


def test_tiny_sd_recording_is_lowest():
    c = classify_source(
        _probed(
            codec="h264",
            profile="High",
            width=854,
            height=480,
            pix_fmt="yuv420p",
            bit_rate=400_000,
        )
    )
    assert c.name == "lowest"


def test_mpeg2_dvd_is_lowest_even_at_high_bitrate():
    c = classify_source(
        _probed(
            codec="mpeg2video",
            profile="Main",
            width=720,
            height=480,
            pix_fmt="yuv420p",
            bit_rate=8_000_000,
        )
    )
    assert c.name == "lowest"
    assert "legacy" in c.reason


def test_1080p_hevc_20mbps_is_master():
    c = classify_source(
        _probed(
            codec="hevc",
            profile="Main 10",
            width=1920,
            height=1080,
            bit_rate=20_000_000,
        )
    )
    assert c.name == "master"


def test_1080p_avc_8mbps_is_webrip():
    c = classify_source(
        _probed(
            codec="h264",
            profile="High",
            width=1920,
            height=1080,
            pix_fmt="yuv420p",
            bit_rate=8_000_000,
        )
    )
    assert c.name == "webrip"


def test_4k_under_stream_ceiling_is_stream():
    c = classify_source(
        _probed(
            codec="h264",
            profile="High",
            pix_fmt="yuv420p",
            bit_rate=6_000_000,
        )
    )
    assert c.name == "stream"
    assert c.video_bps == 6_000_000
    assert c.stream_ceiling_bps == STREAM_CEILING_BPS["2160p"]


def test_explicit_override_wins():
    c = classify_source(_probed(), override="webrip")
    assert c.name == "webrip"
    assert c.override is True
    assert "explicit" in c.reason


def test_unknown_override_raises():
    with pytest.raises(ValueError, match="unknown source class"):
        classify_source(_probed(), override="bluray")


def test_missing_bitrate_defaults_to_webrip():
    c = classify_source(_probed(bit_rate=None, format_bit_rate=None))
    assert c.name == "webrip"
    assert "unknown" in c.reason


def test_apply_source_class_master_is_identity():
    base = presets.PRESETS[("2160p", "film")]
    assert apply_source_class(base, "master") is base


def test_apply_source_class_webrip_raises_cq_and_renames():
    base = presets.PRESETS[("2160p", "film")]
    webrip = apply_source_class(base, "webrip")
    assert webrip.name == "2160p-film+webrip"
    assert webrip.nvenc_cq == base.nvenc_cq + 4
    assert webrip.svt_hdr_crf == base.svt_hdr_crf + 3
    assert webrip.crf == base.crf + 3
    assert webrip is not base


def test_apply_source_class_stream_raises_more_than_webrip():
    base = presets.PRESETS[("2160p", "anime")]
    stream = apply_source_class(base, "stream")
    assert stream.name.endswith("+stream")
    assert stream.nvenc_cq == base.nvenc_cq + 8
    assert stream.svt_hdr_crf == base.svt_hdr_crf + 6


def test_apply_source_class_lowest_raises_most():
    base = presets.PRESETS[("2160p", "film")]
    lowest = apply_source_class(base, "lowest")
    assert lowest.name.endswith("+lowest")
    assert lowest.nvenc_cq == base.nvenc_cq + 12
    assert lowest.svt_hdr_crf == base.svt_hdr_crf + 10
