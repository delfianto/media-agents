from __future__ import annotations

import pytest
from av1transcode import colorinfo

# Real side_data_list fields, captured directly with ffprobe against this
# library's own 4K remux (Mission: Impossible - The Final Reckoning, HEVC
# Main 10, Dolby Vision profile 8) -- not hand-invented numbers, so the
# string-building tests below prove the actual format ffprobe emits round-trips
# correctly rather than just whatever shape the implementation assumed.
REAL_MASTERING_DISPLAY = {
    "red_x": "11408507/16777216",
    "red_y": "5368709/16777216",
    "green_x": "2222981/8388608",
    "green_y": "11576279/16777216",
    "blue_x": "5033165/33554432",
    "blue_y": "16106127/268435456",
    "white_point_x": "10492471/33554432",
    "white_point_y": "689963/2097152",
    "min_luminance": "209800/2098000053",
    "max_luminance": "1000/1",
}
REAL_CONTENT_LIGHT = {"max_content": 992, "max_average": 441}


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("34000/50000", 0.68),
        ("256000/256", 1000.0),
        ("1000/1", 1000.0),
        ("", None),
        (None, None),
        ("not-a-fraction", None),
        ("1/0", None),
    ],
)
def test_parse_fraction(value, expected):
    result = colorinfo.parse_fraction(value)
    if expected is None:
        assert result is None
    else:
        assert result == pytest.approx(expected)


@pytest.mark.parametrize(
    ("transfer", "expected"),
    [
        ("smpte2084", True),
        ("arib-std-b67", True),
        ("bt709", False),
        (None, False),
        ("", False),
    ],
)
def test_is_hdr(transfer, expected):
    assert colorinfo.is_hdr({"color_transfer": transfer}) is expected


def test_has_dolby_vision():
    assert colorinfo.has_dolby_vision({"dolby_vision": {"dv_profile": 8}}) is True
    assert colorinfo.has_dolby_vision({"dolby_vision": None}) is False
    assert colorinfo.has_dolby_vision({}) is False


def test_has_hdr10_plus():
    assert colorinfo.has_hdr10_plus({"hdr10_plus": {"side_data_type": "HDR10+"}}) is True
    assert colorinfo.has_hdr10_plus({"hdr10_plus": None}) is False
    assert colorinfo.has_hdr10_plus({}) is False


def test_needs_dynamic_metadata_path():
    assert colorinfo.needs_dynamic_metadata_path({"dolby_vision": {"dv_profile": 8}}) is True
    assert colorinfo.needs_dynamic_metadata_path({"hdr10_plus": {}}) is True
    assert colorinfo.needs_dynamic_metadata_path({"color_transfer": "smpte2084"}) is False


def test_mastering_display_param_matches_svtav1_cli_format():
    value = colorinfo.mastering_display_param(REAL_MASTERING_DISPLAY)
    assert value is not None
    assert value.startswith("G(")
    assert "B(" in value and "R(" in value and "WP(" in value and "L(" in value
    # Order matters to SvtAv1EncApp: G, B, R, WP, then L(max,min).
    assert (
        value.index("G(")
        < value.index("B(")
        < value.index("R(")
        < value.index("WP(")
        < value.index("L(")
    )
    assert "L(1000.0000," in value


def test_mastering_display_param_missing_field_returns_none():
    incomplete = dict(REAL_MASTERING_DISPLAY)
    del incomplete["max_luminance"]
    assert colorinfo.mastering_display_param(incomplete) is None


def test_content_light_param():
    assert colorinfo.content_light_param(REAL_CONTENT_LIGHT) == "992,441"


def test_content_light_param_missing_field_returns_none():
    assert colorinfo.content_light_param({"max_content": 992}) is None


def test_svtav1_hdr_params_full_hdr10_video():
    video = {
        "color_primaries": "bt2020",
        "color_transfer": "smpte2084",
        "color_space": "bt2020nc",
        "mastering_display": REAL_MASTERING_DISPLAY,
        "content_light": REAL_CONTENT_LIGHT,
    }
    params = colorinfo.svtav1_hdr_params(video)
    assert params["color-primaries"] == "9"
    assert params["transfer-characteristics"] == "16"
    assert params["matrix-coefficients"] == "9"
    assert params["content-light"] == "992,441"
    assert params["mastering-display"].startswith("G(")


def test_svtav1_hdr_params_unmapped_color_tags_are_omitted_not_guessed():
    params = colorinfo.svtav1_hdr_params({"color_primaries": "some-future-cicp-name"})
    assert "color-primaries" not in params
    assert params == {}
